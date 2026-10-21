import type { SourceResult } from "@/lib/source-import";
import type { Tags } from "craft-api/api/types";

export interface ChatSource extends SourceResult { simulated: boolean; tags?: Tags; filename?: string; indexedAt?: string }
export interface ChatSourceResults { sources: ChatSource[]; truncated: boolean }
const MAX_CHARACTERS = 131072;
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown, limit: number): value is string => typeof value === "string" && value.length <= limit;
const label = (value: unknown, limit: number): value is string => text(value, limit) && ![...value].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);
const item = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/.test(value);
const positive = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value > 0;
const classification = (value: unknown): value is ChatSource["classification"] => value === "unclassified" || value === "protected-a" || value === "protected-b";
const only = (value: Record<string, unknown>, names: string[]) => Object.keys(value).every(key => names.includes(key));
const tags = (value: unknown) => record(value) && only(value, ["mission", "silo", "stage"]) && ["mission", "silo", "stage"].every(key => value[key] === null || label(value[key], 512));

function bounded(value: unknown): boolean {
  const pending = [{ value, depth: 0 }]; let nodes = 0, characters = 0;
  while (pending.length) {
    const entry = pending.pop()!;
    if (++nodes > 4096 || entry.depth > 12) return false;
    if (typeof entry.value === "string") characters += entry.value.length;
    else if (Array.isArray(entry.value)) {
      if (entry.value.length > 1000) return false;
      pending.push(...entry.value.map(value => ({ value, depth: entry.depth + 1 })));
    } else if (record(entry.value)) {
      const keys = Object.keys(entry.value);
      if (keys.length > 32) return false;
      characters += keys.join("").length;
      for (const key of keys) pending.push({ value: entry.value[key], depth: entry.depth + 1 });
    }
    if (characters > MAX_CHARACTERS) return false;
  }
  return true;
}

function resultBody(value: unknown): Record<string, unknown> | null {
  // AG UI live/history adapters parse tool-message JSON, retaining MCP envelopes.
  // CRAFT's MCP server supplies the same JSON object as structuredContent and text.
  if (typeof value === "string") {
    if (value.length > MAX_CHARACTERS) return null;
    value = JSON.parse(value);
  }
  if (!record(value) || !bounded(value)) return null;
  if ("content" in value || "structuredContent" in value) {
    if (!only(value, ["content", "structuredContent", "isError", "_meta"]) || value.isError !== undefined && value.isError !== false ||
      !Array.isArray(value.content) || value.content.length > 1) return null;
    if (value.structuredContent !== undefined) return record(value.structuredContent) ? value.structuredContent : null;
    const content = value.content[0];
    if (!record(content) || content.type !== "text" || !text(content.text, MAX_CHARACTERS)) return null;
    value = JSON.parse(content.text);
  }
  return record(value) && bounded(value) ? value : null;
}

function searchHit(value: unknown): ChatSource | null {
  if (!record(value) || !only(value, ["kind", "handle", "title", "headingPath", "text", "score", "tags", "classification", "before", "after"]) ||
    (value.kind !== "document" && value.kind !== "chunk") || !record(value.handle) || !only(value.handle, ["item", "version", "block", "locator", "quoteSha256", "simulated"]) ||
    !item(value.handle.item) || !positive(value.handle.version) || !positive(value.handle.block) || !label(value.handle.locator, 512) ||
    typeof value.handle.quoteSha256 !== "string" || !/^[a-f0-9]{64}$/.test(value.handle.quoteSha256) || typeof value.handle.simulated !== "boolean" ||
    !label(value.title, 4096) || !label(value.headingPath, 512) || !text(value.text, 65536) || !tags(value.tags) ||
    typeof value.score !== "number" || !Number.isFinite(value.score) || !classification(value.classification)) return null;
  const context = value.tags as Tags;
  return { item: value.handle.item, version: value.handle.version, title: value.title.slice(0, 400), snippet: value.text.slice(0, 640), classification: value.classification, simulated: value.handle.simulated, ...(Object.values(context).some(Boolean) ? { tags: context } : {}) };
}

export function documentSource(value: unknown): ChatSource | null {
  if (!record(value) || !only(value, ["item", "version", "title", "path", "tags", "summary", "summaryKind", "outline", "versions", "simulated", "classification", "deleted"]) ||
    !item(value.item) || !positive(value.version) || !label(value.title, 4096) || !label(value.path, 4096) || !tags(value.tags) || !text(value.summary, 65536) ||
    (value.summaryKind !== "extract" && value.summaryKind !== "generated") || typeof value.simulated !== "boolean" || !classification(value.classification) || value.deleted !== false ||
    !Array.isArray(value.outline) || value.outline.length > 1000 || !value.outline.every(entry => record(entry) && only(entry, ["block", "headingPath"]) && positive(entry.block) && label(entry.headingPath, 512)) ||
    !Array.isArray(value.versions) || value.versions.length > 200 || !value.versions.every(entry => record(entry) && only(entry, ["version", "sha256", "indexedAt"]) && positive(entry.version) && typeof entry.sha256 === "string" && /^[a-f0-9]{64}$/.test(entry.sha256) && label(entry.indexedAt, 128)) ||
    !value.versions.some(entry => entry.version === value.version)) return null;
  const context = value.tags as Tags;
  return { item: value.item, version: value.version, title: value.title.slice(0, 400), snippet: value.summary.slice(0, 640), classification: value.classification, simulated: value.simulated, ...(Object.values(context).some(Boolean) ? { tags: context } : {}), filename: value.path.split("/").at(-1), indexedAt: value.versions.find(entry => entry.version === value.version)?.indexedAt };
}

export function chatSourceResults(parts: readonly unknown[]): ChatSourceResults {
  const sources = new Map<string, ChatSource>(), conflicts = new Set<string>();
  let truncated = parts.length > 64;
  for (const part of parts.slice(0, 64)) {
    if (!record(part) || part.type !== "tool-call" || !record(part.status) || part.status.type !== "complete" ||
      part.isError !== undefined && part.isError !== false || (part.toolName !== "search" && part.toolName !== "get_document")) continue;
    try {
      const body = resultBody(part.result);
      if (!body) continue;
      let found: ChatSource[];
      if (part.toolName === "search") {
        if (!only(body, ["hits", "truncated"]) || !Array.isArray(body.hits) || body.hits.length > 8 || typeof body.truncated !== "boolean") continue;
        const hits = body.hits.map(searchHit);
        if (hits.some(hit => hit === null)) continue;
        found = hits as ChatSource[]; truncated ||= body.truncated;
      } else {
        const source = documentSource(body);
        if (!source) continue;
        found = [source];
      }
      for (const source of found) {
        const key = JSON.stringify([source.item, source.version]), previous = sources.get(key);
        if (conflicts.has(key)) continue;
        if (previous && (previous.title !== source.title || previous.classification !== source.classification || previous.simulated !== source.simulated || (["mission", "silo", "stage"] as const).some(tag => previous.tags?.[tag] !== source.tags?.[tag]))) {
          sources.delete(key); conflicts.add(key);
        } else if (!previous) sources.set(key, source);
      }
    } catch { /* Malformed tool JSON never creates an actionable source reference. */ }
  }
  return { sources: [...sources.values()].slice(0, 8), truncated: truncated || sources.size > 8 };
}
