import { apiFetch } from "@/lib/api-client";
import { documentSource, type ChatSource } from "@/lib/chat-source-results";
import type { ChunkText, LibraryDocument } from "craft-api/api/types";

export interface SourceMarkdownProgress { covered: number; total: number }
export type SourceMarkdownResult =
  | { type: "complete"; markdown: string; filename: string; source: ChatSource; chunks: ChunkText[] }
  | { type: "unavailable"; message: string; covered: number; total: number; refused: boolean };

const MAX_BLOCKS = 500;
const MAX_TEXT_BYTES = 2 * 1024 * 1024;
const MAX_JSON_BYTES = 4 * 1024 * 1024;
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const only = (value: Record<string, unknown>, fields: string[]) => Object.keys(value).every(key => fields.includes(key));
const label = (value: unknown, limit: number): value is string => typeof value === "string" && value.length <= limit && ![...value].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);
const escapeMarkdown = (value: string) => value.replace(/[\\`*_{}[\]()<>#+.!|]/g, "\\$&");

class SourceReadError extends Error {
  readonly refused: boolean;
  constructor(refused = false) { super("Indexed text unavailable"); this.refused = refused; }
}

// Bound response bytes before parsing; AbortController also cancels stalled reads.
async function readJSON(url: string, signal: AbortSignal, request: typeof fetch): Promise<unknown> {
  const response = await request(url, { credentials: "same-origin", cache: "no-store", signal });
  if (!response.ok) { await response.body?.cancel(); throw new SourceReadError(true); }
  const length = response.headers.get("Content-Length");
  if (signal.aborted || !response.body || (length !== null && (!/^\d+$/.test(length) || Number(length) > MAX_JSON_BYTES))) {
    await response.body?.cancel(); throw new SourceReadError();
  }
  const reader = response.body.getReader(), decoder = new TextDecoder("utf-8", { fatal: true });
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", cancel, { once: true });
  let bytes = 0, value = "";
  try {
    while (true) {
      if (signal.aborted) throw new SourceReadError();
      const chunk = await reader.read();
      if (signal.aborted) throw new SourceReadError();
      if (chunk.done) break;
      bytes += chunk.value.byteLength;
      if (bytes > MAX_JSON_BYTES) { await reader.cancel(); throw new SourceReadError(); }
      value += decoder.decode(chunk.value, { stream: true });
    }
    return JSON.parse(value + decoder.decode());
  } finally {
    signal.removeEventListener("abort", cancel);
    reader.releaseLock();
  }
}

function verifiedDocument(value: unknown, item: string, version: number): LibraryDocument {
  const source = documentSource(value);
  if (!source || source.item !== item || source.version !== version) throw new SourceReadError();
  const document = value as LibraryDocument;
  if (!document.outline.length || document.outline.length > MAX_BLOCKS ||
    !document.outline.every((entry, index) => entry.block === index + 1) ||
    document.versions.filter(entry => entry.version === version).length !== 1) throw new SourceReadError();
  return document;
}

function identity(document: LibraryDocument): string {
  return JSON.stringify([document.item, document.version, document.title, document.path, document.tags,
    document.classification, document.simulated, document.outline, document.versions.find(entry => entry.version === document.version)]);
}

function verifiedChunk(value: unknown, document: LibraryDocument, index: number): ChunkText {
  const outline = document.outline[index];
  if (!outline || !record(value) || !only(value, ["handle", "headingPath", "text", "score"]) ||
    value.headingPath !== outline.headingPath || typeof value.text !== "string" || !value.text.length || value.text.length > 65536 || value.text.includes("\0") ||
    value.score !== undefined && (typeof value.score !== "number" || !Number.isFinite(value.score))) throw new SourceReadError();
  const handle = value.handle;
  if (!record(handle) || !only(handle, ["item", "version", "block", "locator", "quoteSha256", "simulated"]) ||
    handle.item !== document.item || handle.version !== document.version || handle.block !== outline.block || handle.simulated !== document.simulated ||
    !label(handle.locator, 512) || typeof handle.quoteSha256 !== "string" || !/^[a-f0-9]{64}$/.test(handle.quoteSha256)) throw new SourceReadError();
  return value as unknown as ChunkText;
}

function assemble(document: LibraryDocument, chunks: ChunkText[]): string {
  const source = documentSource(document)!;
  const provenance = [
    `# ${escapeMarkdown(document.title)}`,
    "",
    `- Filename: ${escapeMarkdown(source.filename ?? "Unknown")}`,
    `- Version: ${document.version}`,
    `- Classification: ${document.classification}`,
    `- Simulated: ${document.simulated ? "Yes" : "No"}`,
    `- Last indexed: ${escapeMarkdown(source.indexedAt ?? "Unknown")}`,
    `- Coverage: ${chunks.length}/${document.outline.length} indexed blocks`,
    "- Provenance: Indexed extracted text for this selected source version.",
    "",
    "> All indexed text; layout/images may be omitted. This reconstructed Markdown is not a lossless conversion of the original document.",
    "",
  ];
  // Preserve every returned text block, including repeated table headers.
  for (const chunk of chunks) provenance.push(
    `## Indexed block ${chunk.handle.block}${chunk.headingPath ? `: ${escapeMarkdown(chunk.headingPath)}` : ""}`,
    "", chunk.text, "",
  );
  return provenance.join("\n");
}

export async function loadSourceMarkdown(
  item: string,
  version: number,
  signal: AbortSignal,
  onProgress?: (progress: SourceMarkdownProgress) => void,
  request: typeof fetch = apiFetch,
): Promise<SourceMarkdownResult> {
  const controller = new AbortController(), chunks: ChunkText[] = [], encoder = new TextEncoder();
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, 30000);
  let total = 0, textBytes = 0;
  try {
    if (signal.aborted || !/^[A-Za-z0-9_-]{1,128}$/.test(item) || !Number.isSafeInteger(version) || version < 1) throw new SourceReadError();
    const base = `/api/library/documents/${encodeURIComponent(item)}`;
    const document = verifiedDocument(await readJSON(`${base}?version=${version}`, controller.signal, request), item, version);
    total = document.outline.length;
    onProgress?.({ covered: 0, total });
    for (let offset = 0; offset < total; offset += 20) {
      const count = Math.min(20, total - offset);
      const from = document.outline[offset]!.block, to = document.outline[offset + count - 1]!.block;
      const page = await readJSON(`${base}/chunks?version=${version}&fromBlock=${from}&toBlock=${to}`, controller.signal, request);
      if (!record(page) || !only(page, ["chunks", "truncated"]) || page.truncated !== false || !Array.isArray(page.chunks) || page.chunks.length !== count) throw new SourceReadError();
      for (let index = 0; index < count; index++) {
        const chunk = verifiedChunk(page.chunks[index], document, offset + index);
        const bytes = encoder.encode(chunk.text);
        textBytes += bytes.byteLength;
        if (textBytes > MAX_TEXT_BYTES) throw new SourceReadError();
        const digest = await crypto.subtle.digest("SHA-256", bytes);
        const hash = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
        if (controller.signal.aborted || hash !== chunk.handle.quoteSha256) throw new SourceReadError();
        chunks.push(chunk);
      }
      onProgress?.({ covered: chunks.length, total });
    }
    // Recheck current access and pinned metadata before making a complete claim.
    const confirmed = verifiedDocument(await readJSON(`${base}?version=${version}`, controller.signal, request), item, version);
    if (controller.signal.aborted || identity(confirmed) !== identity(document) || chunks.length !== total || !chunks.some(chunk => chunk.text.trim())) throw new SourceReadError();
    const markdown = assemble(document, chunks);
    if (encoder.encode(markdown).byteLength > MAX_TEXT_BYTES) throw new SourceReadError();
    const source = documentSource(document)!;
    const name = (source.filename ?? "source").replace(/\.[^.]+$/, "").replace(/[<>:"/\\|?*]/g, "_").slice(0, 100) || "source";
    return { type: "complete", markdown, filename: `${name}.v${version}.indexed.md`, source, chunks };
  } catch (error) {
    return {
      type: "unavailable", covered: chunks.length, total,
      message: controller.signal.aborted ? "The indexed-text read was cancelled or exceeded 30 seconds. Reopen the preview to try again." :
        "Complete indexed text is unavailable. Its outline, text or coverage could not be verified within the 500-block and 2 MiB limits. Open or download the original document.",
      refused: error instanceof SourceReadError && error.refused,
    };
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
    controller.abort();
  }
}
