import { apiFetch } from "@/lib/api-client";
import type { CitationSource } from "@/components/citations/citation-context";
import type { ToolCallMessagePartStatus } from "@assistant-ui/react";

export interface CitationHandle {
  item: string;
  version: number;
  block: number;
  locator: string;
  quoteSha256: string;
  simulated: boolean;
}
type Classification = "unclassified" | "protected-a" | "protected-b";
export interface AnswerCitation {
  handle: CitationHandle;
  title: string;
  headingPath: string;
  classification: Classification;
  quote: string;
}
export interface AnswerEvidence {
  citations: AnswerCitation[];
  confidence: { score: number; level: "high" | "medium" | "low"; reason: string };
}
export interface CheckedSource extends CitationSource {
  classification: Classification;
  quote: string;
}
export interface ToolView {
  name: string;
  correlationId?: string;
  status: "Running" | "Completed" | "Failed" | "Awaiting review" | "Unavailable";
  hitCount?: number;
}

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const bounded = (value: unknown, max: number): value is string =>
  typeof value === "string" && value.length <= max && !Array.from(value).some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);
const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value > 0;
const classification = (value: unknown): value is Classification =>
  value === "unclassified" || value === "protected-a" || value === "protected-b";
const only = (value: Record<string, unknown>, fields: readonly string[]) =>
  Object.keys(value).every((key) => fields.includes(key));

function parseCitation(value: unknown): AnswerCitation | null {
  if (!record(value) || !only(value, ["handle", "title", "headingPath", "classification", "quote"])) return null;
  const h = value.handle;
  if (!record(h) || !only(h, ["item", "version", "block", "locator", "quoteSha256", "simulated"]) ||
    typeof h.item !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(h.item) ||
    !positive(h.version) || !positive(h.block) || !bounded(h.locator, 512) ||
    typeof h.quoteSha256 !== "string" || !/^[a-f0-9]{64}$/.test(h.quoteSha256) ||
    typeof h.simulated !== "boolean" || !bounded(value.title, 240) || !value.title ||
    !bounded(value.headingPath, 512) || !classification(value.classification) ||
    typeof value.quote !== "string" || value.quote.length > 65536 || value.quote.includes("\0")) return null;
  return {
    handle: { item: h.item, version: h.version, block: h.block, locator: h.locator, quoteSha256: h.quoteSha256, simulated: h.simulated },
    title: value.title, headingPath: value.headingPath, classification: value.classification, quote: value.quote,
  };
}

export function answerEvidence(parts: readonly unknown[]): AnswerEvidence[] {
  const answers: AnswerEvidence[] = [];
  for (const part of parts) {
    if (!record(part) || part.type !== "data" || part.name !== "agui-activity/craft.answer") continue;
    const data = part.data;
    if (!record(data) || !only(data, ["messageId", "citations", "confidence"]) || !Array.isArray(data.citations) || data.citations.length > 8) continue;
    const c = data.confidence;
    if (!record(c) || !only(c, ["score", "level", "reason"]) || typeof c.score !== "number" ||
      !Number.isFinite(c.score) || c.score < 0 || c.score > 1 ||
      (c.level !== "high" && c.level !== "medium" && c.level !== "low") || !bounded(c.reason, 240)) continue;
    const citations = data.citations.map(parseCitation);
    if (citations.some((citation) => citation === null)) continue;
    answers.push({ citations: citations as AnswerCitation[], confidence: { score: c.score, level: c.level, reason: c.reason } });
  }
  return answers;
}

export function citationKey(handle: CitationHandle): string {
  return JSON.stringify([handle.item, handle.version, handle.block, handle.locator, handle.quoteSha256, handle.simulated]);
}

export async function checkSource(citation: AnswerCitation, signal: AbortSignal, fetcher: typeof fetch = apiFetch): Promise<CheckedSource | null> {
  const valid = parseCitation(citation);
  if (!valid || signal.aborted) return null;
  try {
    const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(valid.quote));
    const hex = Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
    if (hex !== valid.handle.quoteSha256 || signal.aborted) return null;
    const checked = await fetcher("/api/library/citations/check", {
      method: "POST", credentials: "same-origin", cache: "no-store", signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ handle: valid.handle }),
    });
    if (!checked.ok || signal.aborted) return null;
    const status: unknown = await checked.json();
    if (signal.aborted || !record(status) || !only(status, ["status", "latestVersion"]) || status.status !== "current" || status.latestVersion !== valid.handle.version) return null;
    const base = `/api/library/documents/${encodeURIComponent(valid.handle.item)}`;
    const response = await fetcher(`${base}?version=${valid.handle.version}`, { credentials: "same-origin", cache: "no-store", signal });
    if (!response.ok || signal.aborted) return null;
    const document: unknown = await response.json();
    if (signal.aborted || !record(document) || !only(document, ["item", "version", "title", "path", "tags", "summary", "summaryKind", "outline", "versions", "simulated", "classification", "deleted"]) ||
      document.item !== valid.handle.item || document.version !== valid.handle.version || document.deleted !== false ||
      document.title !== valid.title || document.classification !== valid.classification || document.simulated !== valid.handle.simulated ||
      !bounded(document.path, 4096) || !record(document.tags) || typeof document.summary !== "string" ||
      (document.summaryKind !== "extract" && document.summaryKind !== "generated") || !Array.isArray(document.outline) || !Array.isArray(document.versions)) return null;
    if (!only(document.tags, ["mission", "silo", "stage"]) ||
      !["mission", "silo", "stage"].every((field) => document.tags && record(document.tags) && (document.tags[field] === null || bounded(document.tags[field], 512))) ||
      !document.outline.every((entry: unknown) => record(entry) && only(entry, ["block", "headingPath"]) && positive(entry.block) && bounded(entry.headingPath, 512)) ||
      !document.versions.every((entry: unknown) => record(entry) && only(entry, ["version", "sha256", "indexedAt"]) && positive(entry.version) && typeof entry.sha256 === "string" && /^[a-f0-9]{64}$/.test(entry.sha256) && bounded(entry.indexedAt, 128))) return null;
    const query = `version=${valid.handle.version}&locator=${encodeURIComponent(valid.handle.locator)}`;
    const previewURL = `${base}/preview?${query}`;
    return {
      id: citationKey(valid.handle), url: previewURL, number: 0,
      title: valid.title, mediaType: "", previewURL, downloadURL: `${base}/download?${query}`,
      sourceID: valid.handle.item, version: String(valid.handle.version), artifactID: "",
      simulated: valid.handle.simulated, classification: valid.classification,
      locator: valid.handle.locator, locationLabel: valid.headingPath || valid.handle.locator,
      subtitle: `${valid.classification} · version ${valid.handle.version}${valid.handle.simulated ? " · Simulated" : ""}`,
      quote: valid.quote.slice(0, 640),
    };
  } catch {
    return null;
  }
}

type CodeExecutionStatus = "ok" | "error" | "unknown";
// Allows escaped 64 KiB output in paired text/structured MCP envelopes.
const MAX_CODE_RESULT_CHARACTERS = 2 * 1024 * 1024;

function codeExecutionStatus(value: unknown): CodeExecutionStatus {
  try {
    // Workspace actions can be wrapped in nested MCP envelopes in live/history.
    // Inspect only bounded results; never surface kernel error or output text.
    for (let depth = 0; depth < 4; depth++) {
      if (typeof value === "string") {
        if (value.length > MAX_CODE_RESULT_CHARACTERS) return "unknown";
        value = JSON.parse(value);
      }
      if (!record(value)) return "unknown";
      if (value.isError === true) return "error";
      if ((value.status === "ok" || value.status === "error") && only(value, ["status", "outputs", "error", "truncated"]) &&
        typeof value.truncated === "boolean" && (value.error === null || typeof value.error === "string" && value.error.length <= MAX_CODE_RESULT_CHARACTERS) &&
        Array.isArray(value.outputs)) return value.status;
      if (record(value.structuredContent)) value = value.structuredContent;
      else if (Array.isArray(value.content) && value.content.length === 1 && record(value.content[0]) && value.content[0].type === "text") value = value.content[0].text;
      else return "unknown";
    }
  } catch { /* Malformed tool JSON supplies no execution-status claim. */ }
  return "unknown";
}

export function toolView(part: unknown): ToolView {
  const p = record(part) ? part : {};
  const name = typeof p.toolName === "string" && /^[A-Za-z0-9_.:-]{1,80}$/.test(p.toolName) ? p.toolName : "Tool";
  const correlationId = typeof p.toolCallId === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(p.toolCallId) ? p.toolCallId : undefined;
  const status = record(p.status) ? p.status.type as ToolCallMessagePartStatus["type"] : undefined;
  const execution = status === "complete" && name === "run_code" && p.isError !== true ? codeExecutionStatus(p.result) : undefined;
  const completed = p.isError === true || execution === "error" ? "Failed" : execution === "unknown" ? "Unavailable" : "Completed";
  return {
    name, correlationId,
    status: status === "running" ? "Running" : status === "complete" ? completed : status === "incomplete" ? "Failed" : status === "requires-action" ? "Awaiting review" : "Unavailable",
  };
}

const TOOL_LABELS: Record<string, string> = {
  search: "Searched the library", get_document: "Opened a document", read_chunks: "Read passages", explore: "Read around a passage",
  compare_versions: "Compared versions", list_documents: "Listed documents", check_citation: "Checked a citation",
  list_missions: "Listed missions", list_parameters: "Listed parameters", find_missions: "Found missions", get_mission: "Read mission values",
  compare_missions: "Compared missions", similar_missions: "Found similar missions", explain_similarity: "Explained a similarity",
  read_facets: "Read mission profiles", list_capabilities: "Listed tools", check_calculation: "Checked a calculation",
  list_files: "Listed your files", list_notebooks: "Listed your notebooks", read_notebook: "Read a notebook",
  run_code: "Ran code in your workspace", write_file: "Wrote a file", edit_notebook: "Edited a notebook",
};

/** A plain-language label for one or more tool calls. */
export function toolLabel(names: readonly string[]): string {
  const short = names.map(name => name.slice(name.lastIndexOf(".") + 1));
  const labels = [...new Set(short.map(name => TOOL_LABELS[name] ?? "Used " + name.replaceAll("_", " ")))];
  return labels.length ? labels.join(" · ") : "Worked";
}
