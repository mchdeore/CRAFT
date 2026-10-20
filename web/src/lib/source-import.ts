import { apiFetch } from "@/lib/api-client";
import { resolveWorkspaceURL, type WorkspaceConfiguration } from "@/components/workspace-url";
import type { LibraryHit, LibrarySearch } from "craft-api/api/types";

export type SourceResult = Pick<LibraryHit, "title" | "classification"> & {
  item: string; version: number; snippet: string;
};
type FailureCode = "invalid_query" | "invalid_source" | "invalid_workspace" | "invalid_response" | "invalid_name"
  | "craft_auth" | "workspace_auth" | "search_unavailable" | "import_unavailable" | "metadata_unavailable" | "too_large" | "conflict";
const messages: Record<FailureCode, string> = {
  invalid_query: "Enter a source search of up to 500 characters.",
  invalid_source: "This source reference is unavailable. Search again.",
  invalid_workspace: "Your workspace address is unavailable. Reconnect the workspace before importing.",
  invalid_response: "The service returned an unexpected response. Try again.",
  invalid_name: "The source filename cannot be safely imported.",
  craft_auth: "Your CRAFT sign-in expired. Sign in again, then retry.",
  workspace_auth: "Sign in to your JupyterLab workspace, then retry Import.",
  search_unavailable: "Source search is unavailable. Try again.",
  import_unavailable: "Import could not finish. Open JupyterLab to check your sign-in, then retry.",
  metadata_unavailable: "Source metadata could not be confirmed. Open the copy in JupyterLab before importing again.",
  too_large: "This source exceeds the 16 MB import limit.",
  conflict: "A personal filename could not be reserved. Open JupyterLab before trying again.",
};
export class SourceImportFailure extends Error {
  code: FailureCode;
  readonly copiedPath?: string;
  constructor(code: FailureCode, copiedPath?: string) {
    super(messages[code]); this.code = code;
    if (safePersonalPath(copiedPath)) this.copiedPath = copiedPath;
  }
}
export const MAX_IMPORT_BYTES = 16 * 1024 * 1024;
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const safeItem = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/.test(value);
const positiveVersion = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value > 0;
const classification = (value: unknown) => value === "unclassified" || value === "protected-a" || value === "protected-b";
const hasControl = (value: string) => [...value].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);
const cancelled = (signal: AbortSignal) => { if (signal.aborted) throw new DOMException("Request cancelled", "AbortError"); };

export async function searchSources(query: string, signal: AbortSignal): Promise<{ results: SourceResult[]; truncated: boolean }> {
  if (!query.trim() || query.length > 500 || hasControl(query)) throw new SourceImportFailure("invalid_query");
  cancelled(signal);
  const request: LibrarySearch = { query: query.trim(), kinds: ["document"], limit: 6 };
  try {
    const response = await apiFetch("/api/library/search", {
      method: "POST", credentials: "same-origin", redirect: "error", cache: "no-store", signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
    });
    cancelled(signal);
    if (response.status === 401) throw new SourceImportFailure("craft_auth");
    if (!response.ok) throw new SourceImportFailure("search_unavailable");
    const body: unknown = await response.json();
    cancelled(signal);
    if (!record(body) || !Array.isArray(body.hits) || body.hits.length > 6 || typeof body.truncated !== "boolean") throw new SourceImportFailure("invalid_response");
    const results: SourceResult[] = [];
    for (const hit of body.hits) {
      if (!record(hit) || hit.kind !== "document" || !record(hit.handle) || !safeItem(hit.handle.item) || !positiveVersion(hit.handle.version)
        || typeof hit.title !== "string" || typeof hit.text !== "string" || !classification(hit.classification)) throw new SourceImportFailure("invalid_response");
      const item = hit.handle.item, version = hit.handle.version;
      if (results.some(result => result.item === item && result.version === version)) continue;
      results.push({ item, version, title: hit.title.slice(0, 400), snippet: hit.text.slice(0, 640), classification: hit.classification as LibraryHit["classification"] });
    }
    return { results, truncated: body.truncated };
  } catch (error) {
    cancelled(signal);
    throw error instanceof SourceImportFailure ? error : new SourceImportFailure("search_unavailable");
  }
}

function workspaceOrigin(configuration: WorkspaceConfiguration): string {
  try {
    if (configuration.status !== "configured") throw new Error();
    const url = new URL(configuration.url);
    if (url.pathname !== "/lab") throw new Error();
    url.pathname = "/";
    const checked = resolveWorkspaceURL(url.href, globalThis.location?.origin ?? "");
    if (checked.status !== "configured" || checked.url !== configuration.url) throw new Error();
    return url.origin;
  } catch { throw new SourceImportFailure("invalid_workspace"); }
}

function xsrfCookie(): string {
  try {
    const cookies = document.cookie.split(";").map(value => value.trim()).filter(value => value.startsWith("_xsrf="));
    if (cookies.length !== 1) throw new Error();
    const value = decodeURIComponent(cookies[0].slice(6)).replace(/^"(.*)"$/, "$1");
    if (!/^[A-Za-z0-9_|-]{1,512}$/.test(value)) throw new Error();
    return value;
  } catch { throw new SourceImportFailure("workspace_auth"); }
}

const extensions: Record<string, string> = {
  "application/pdf": ".pdf", "text/plain": ".txt", "text/markdown": ".md", "application/json": ".json",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
};
export function personalFilename(headers: Headers, title: string): string {
  const disposition = headers.get("content-disposition") ?? "";
  if (disposition.length > 1024 || hasControl(disposition)) throw new SourceImportFailure("invalid_name");
  const encoded = disposition.match(/\bfilename\*=UTF-8''([^;\s]+)/i);
  const plain = disposition.match(/\bfilename=(?:"([^"]*)"|([^;\s]+))/i);
  let name: string | undefined;
  try { name = encoded ? decodeURIComponent(encoded[1]) : plain?.[1] ?? plain?.[2]; }
  catch { throw new SourceImportFailure("invalid_name"); }
  if ((/\bfilename\*?=/i.test(disposition) && !name) || (name && (name.length > 255 || /^[.]/.test(name) || /[/\\:%]/.test(name) || hasControl(name)))) throw new SourceImportFailure("invalid_name");
  const base = name ?? title;
  const suffix = base.match(/\.([A-Za-z0-9]{1,10})$/);
  const extension = suffix ? `.${suffix[1].toLowerCase()}` : extensions[(headers.get("content-type") ?? "").split(";", 1)[0].trim().toLowerCase()] ?? ".bin";
  const stem = (suffix ? base.slice(0, -suffix[0].length) : base).normalize("NFKD").replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "source";
  return `${stem}${extension}`;
}

async function boundedBytes(response: Response, signal: AbortSignal): Promise<Uint8Array<ArrayBuffer>> {
  const length = response.headers.get("content-length");
  if (length && (!/^\d+$/.test(length) || Number(length) > MAX_IMPORT_BYTES)) throw new SourceImportFailure(/^\d+$/.test(length) ? "too_large" : "invalid_response");
  if (!response.body) throw new SourceImportFailure("invalid_response");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = []; let total = 0;
  try {
    while (true) {
      cancelled(signal);
      const { done, value } = await reader.read();
      cancelled(signal);
      if (done) break;
      total += value.byteLength;
      if (total > MAX_IMPORT_BYTES) throw new SourceImportFailure("too_large");
      chunks.push(value);
    }
  } catch (error) { await reader.cancel().catch(() => undefined); throw error; }
  finally { reader.releaseLock(); }
  const bytes = new Uint8Array(total); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}
function base64(bytes: Uint8Array): string {
  const chunks: string[] = [];
  // Complete groups of three bytes avoid padding between bounded chunks.
  for (let offset = 0; offset < bytes.length; offset += 24576) chunks.push(btoa(String.fromCharCode(...bytes.subarray(offset, offset + 24576))));
  return chunks.join("");
}
function safePersonalPath(value: unknown): value is string {
  return typeof value === "string" && /^imports\/[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$/.test(value) && !value.includes("..");
}
const safeMetadataPath = (value: unknown): value is string => typeof value === "string" && /^imports\/source-metadata\/[A-Za-z0-9][A-Za-z0-9_.-]{0,114}\.json$/.test(value) && !value.includes("..");
async function fileModel(response: Response, signal: AbortSignal, metadata = false): Promise<string> {
  const model: unknown = await response.json(); cancelled(signal);
  if (!record(model) || model.type !== "file" || typeof model.path !== "string" || !(metadata ? safeMetadataPath(model.path) : safePersonalPath(model.path))) throw new SourceImportFailure("invalid_response");
  return model.path;
}

export async function importSource(source: SourceResult, configuration: WorkspaceConfiguration, signal: AbortSignal): Promise<string> {
  if (!safeItem(source.item) || !positiveVersion(source.version) || typeof source.title !== "string" || !classification(source.classification)) throw new SourceImportFailure("invalid_source");
  const origin = workspaceOrigin(configuration);
  const xsrf = xsrfCookie();
  let copiedPath: string | undefined;
  cancelled(signal);
  const contents = async (path: string, method: "GET" | "POST" | "PATCH" | "PUT", body?: object) => {
    cancelled(signal);
    const response = await fetch(`${origin}/api/contents/${path}`, {
      method, credentials: "include", redirect: "error", cache: "no-store", signal,
      ...(body ? { headers: { "Content-Type": "application/json", "X-XSRFToken": xsrf }, body: JSON.stringify(body) } : {}),
    });
    cancelled(signal);
    if (response.status === 401 || response.status === 403) throw new SourceImportFailure("workspace_auth");
    if (response.status === 409) throw new SourceImportFailure("conflict");
    return response;
  };
  try {
    const directory = await contents("imports?content=0", "GET");
    if (directory.status !== 404) {
      if (!directory.ok) throw new SourceImportFailure("import_unavailable");
      const model: unknown = await directory.json(); cancelled(signal);
      if (!record(model) || model.type !== "directory" || model.path !== "imports") throw new SourceImportFailure("invalid_response");
    }
    const original = await apiFetch(`/api/library/documents/${encodeURIComponent(source.item)}/download?version=${source.version}`, {
      method: "GET", credentials: "same-origin", redirect: "error", cache: "no-store", signal,
    });
    let name: string, bytes: Uint8Array<ArrayBuffer>;
    try {
      cancelled(signal);
      if (original.status === 401) throw new SourceImportFailure("craft_auth");
      if (!original.ok) throw new SourceImportFailure("import_unavailable");
      name = personalFilename(original.headers, source.title);
      bytes = await boundedBytes(original, signal);
    } finally {
      // Early header, status or abort rejection must also stop the original transfer.
      await original.body?.cancel().catch(() => undefined);
    }
    cancelled(signal);
    if (directory.status === 404) {
      const created = await contents("imports", "PUT", { type: "directory" });
      if (!created.ok) throw new SourceImportFailure("import_unavailable");
    }
    // POST creates a fresh server-selected file; PATCH refuses an existing destination.
    const created = await contents("imports", "POST", { type: "file", ext: name.slice(name.lastIndexOf(".")) });
    if (created.status !== 201) throw new SourceImportFailure("import_unavailable");
    const fresh = await fileModel(created, signal);
    let target: string | undefined;
    const extensionAt = name.lastIndexOf(".");
    for (let attempt = 0; attempt < 20; attempt++) {
      const candidate = `imports/${attempt === 0 ? name : `${name.slice(0, extensionAt)}-${attempt + 1}${name.slice(extensionAt)}`}`;
      try {
        const renamed = await contents(fresh, "PATCH", { path: candidate });
        if (!renamed.ok) throw new SourceImportFailure("import_unavailable");
        if (await fileModel(renamed, signal) !== candidate) throw new SourceImportFailure("invalid_response");
        target = candidate; break;
      } catch (error) {
        if (!(error instanceof SourceImportFailure) || error.code !== "conflict") throw error;
      }
    }
    if (!target) throw new SourceImportFailure("conflict");
    const saved = await contents(target, "PUT", { type: "file", format: "base64", content: base64(bytes) });
    if (!saved.ok || await fileModel(saved, signal) !== target) throw new SourceImportFailure("invalid_response");
    copiedPath = target;

    // An informational record of this copy, separate from the editable file name.
    const importId = crypto.randomUUID();
    const sha256 = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(byte => byte.toString(16).padStart(2, "0")).join("");
    const metadata = JSON.stringify({
      format: "craft-source-import/1", importId, importedAt: new Date().toISOString(),
      source: { item: source.item, version: source.version, title: source.title.slice(0, 400), classification: source.classification },
      copy: { path: target, sha256, sizeBytes: bytes.byteLength },
    });
    if (new TextEncoder().encode(metadata).byteLength > 4096) throw new SourceImportFailure("metadata_unavailable");
    const metadataDirectory = await contents("imports/source-metadata?content=0", "GET");
    if (metadataDirectory.status === 404) {
      const created = await contents("imports/source-metadata", "PUT", { type: "directory" });
      if (!created.ok) throw new SourceImportFailure("import_unavailable");
      const model: unknown = await created.json(); cancelled(signal);
      if (!record(model) || model.type !== "directory" || model.path !== "imports/source-metadata") throw new SourceImportFailure("invalid_response");
    } else {
      if (!metadataDirectory.ok) throw new SourceImportFailure("import_unavailable");
      const model: unknown = await metadataDirectory.json(); cancelled(signal);
      if (!record(model) || model.type !== "directory" || model.path !== "imports/source-metadata") throw new SourceImportFailure("invalid_response");
    }
    const metadataFile = await contents("imports/source-metadata", "POST", { type: "file", ext: ".json" });
    if (metadataFile.status !== 201) throw new SourceImportFailure("import_unavailable");
    const freshMetadata = await fileModel(metadataFile, signal, true);
    const metadataPath = `imports/source-metadata/${importId}.json`;
    const metadataRename = await contents(freshMetadata, "PATCH", { path: metadataPath });
    if (!metadataRename.ok || await fileModel(metadataRename, signal, true) !== metadataPath) throw new SourceImportFailure("invalid_response");
    const metadataSaved = await contents(metadataPath, "PUT", { type: "file", format: "text", content: metadata });
    if (!metadataSaved.ok || await fileModel(metadataSaved, signal, true) !== metadataPath) throw new SourceImportFailure("invalid_response");
    return target;
  } catch (error) {
    if (copiedPath) throw new SourceImportFailure("metadata_unavailable", copiedPath);
    cancelled(signal);
    throw error instanceof SourceImportFailure ? error : new SourceImportFailure("import_unavailable");
  }
}
