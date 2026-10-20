import { resolveWorkspaceURL, type WorkspaceConfiguration } from "@/components/workspace-url";

type FailureCode = "invalid_workspace" | "invalid_path" | "invalid_response" | "workspace_auth" | "not_found" | "conflict" | "too_large" | "unavailable" | "save_unconfirmed";
const messages: Record<FailureCode, string> = {
  invalid_workspace: "Your personal workspace is not connected.",
  invalid_path: "Choose a file inside your personal workspace. Skills need a simple .md filename.",
  invalid_response: "The workspace returned an unexpected file response.",
  workspace_auth: "Open JupyterLab and sign in to your workspace, then try again.",
  not_found: "This personal file is no longer available. Refresh the file list.",
  conflict: "That filename already exists. Load it to edit, or choose another filename. The existing file was not replaced.",
  too_large: "This file exceeds the workspace browser limit (10 MB, or 200 KB for skill documents).",
  unavailable: "Your personal workspace could not be read. Open JupyterLab to check the connection.",
  save_unconfirmed: "Save could not be confirmed. Inspect the file in JupyterLab before saving again; your edits remain here.",
};
export class PersonalWorkspaceError extends Error {
  readonly code: FailureCode;
  constructor(code: FailureCode) { super(messages[code]); this.code = code; }
}
export const MAX_PERSONAL_BYTES = 10 * 1024 * 1024;
export const MAX_SKILL_BYTES = 200000;
const MAX_DIRECTORY_BYTES = 512 * 1024;
export interface PersonalEntry { name: string; path: string; type: "file" | "notebook" | "directory"; writable: boolean; size: number | null }
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const cancelled = (signal: AbortSignal) => { if (signal.aborted) throw new DOMException("Request cancelled", "AbortError"); };
const parentPath = (path: string) => path.split("/").slice(0, -1).join("/");

function safePath(path: string, root = false): boolean {
  return typeof path === "string" && (root && path === "" || path.length > 0 && path.length <= 1024 && path.split("/").length <= 16 && path.split("/").every(part =>
    part.length > 0 && !part.startsWith(".") && new TextEncoder().encode(part).byteLength <= 255 && !/[\\%?#]/.test(part) && [...part].every(character => character.charCodeAt(0) >= 32 && character.charCodeAt(0) !== 127)));
}
function checkedPath(path: string, root = false): string {
  if (!safePath(path, root)) throw new PersonalWorkspaceError("invalid_path");
  return path;
}
export function personalAbsolutePath(path: string): string { return `/workspace/${checkedPath(path)}`; }
export function personalWorkspaceOrigin(configuration: WorkspaceConfiguration): string {
  try {
    if (configuration.status !== "configured") throw new Error();
    const parsed = new URL(configuration.url);
    const checked = resolveWorkspaceURL(`${parsed.origin}/`, window.location.origin);
    if (checked.status !== "configured" || checked.url !== configuration.url) throw new Error();
    return parsed.origin;
  } catch { throw new PersonalWorkspaceError("invalid_workspace"); }
}
function xsrfCookie(): string {
  try {
    const cookies = document.cookie.split(";").map(part => part.trim()).filter(part => part.startsWith("_xsrf="));
    if (cookies.length !== 1) throw new Error();
    const token = decodeURIComponent(cookies[0]!.slice(6)).replace(/^"(.*)"$/, "$1");
    if (!/^[A-Za-z0-9._|-]{1,512}$/.test(token)) throw new Error();
    return token;
  } catch { throw new PersonalWorkspaceError("workspace_auth"); }
}
async function boundedJSON(response: Response, signal: AbortSignal, limit: number): Promise<unknown> {
  const length = response.headers.get("content-length");
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > limit)) {
    await response.body?.cancel();
    throw new PersonalWorkspaceError(/^\d+$/.test(length) ? "too_large" : "invalid_response");
  }
  if (!response.body) throw new PersonalWorkspaceError("invalid_response");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = []; let size = 0;
  const onAbort = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", onAbort, { once: true });
  try {
    while (true) {
      cancelled(signal);
      const chunk = await reader.read(); cancelled(signal);
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > limit) throw new PersonalWorkspaceError("too_large");
      chunks.push(chunk.value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown; }
    catch { throw new PersonalWorkspaceError("invalid_response"); }
  } catch (error) { await reader.cancel().catch(() => {}); throw error; }
  finally { signal.removeEventListener("abort", onAbort); reader.releaseLock(); }
}
async function contents(configuration: WorkspaceConfiguration, path: string, signal: AbortSignal, options: { method?: "GET" | "POST" | "PATCH" | "PUT"; query?: string; body?: unknown; limit?: number; missing?: boolean } = {}): Promise<unknown> {
  cancelled(signal); checkedPath(path, true);
  const origin = personalWorkspaceOrigin(configuration);
  const method = options.method ?? "GET";
  const headers = method === "GET" ? undefined : { "Content-Type": "application/json", "X-XSRFToken": xsrfCookie() };
  try {
    const response = await fetch(`${origin}/api/contents/${path.split("/").map(encodeURIComponent).join("/")}${options.query ?? ""}`, {
      method, credentials: "include", redirect: "error", cache: "no-store", signal,
      ...(headers ? { headers, body: JSON.stringify(options.body) } : {}),
    });
    cancelled(signal);
    if (response.status === 401 || response.status === 403) throw new PersonalWorkspaceError("workspace_auth");
    if (response.status === 409) throw new PersonalWorkspaceError("conflict");
    if (response.status === 404) { if (options.missing) return null; throw new PersonalWorkspaceError("not_found"); }
    if (!response.ok) throw new PersonalWorkspaceError("unavailable");
    if (method === "POST" && response.status !== 201) throw new PersonalWorkspaceError("invalid_response");
    const body = await boundedJSON(response, signal, options.limit ?? MAX_DIRECTORY_BYTES); cancelled(signal); return body;
  } catch (error) {
    cancelled(signal);
    throw error instanceof PersonalWorkspaceError ? error : new PersonalWorkspaceError("unavailable");
  }
}
function directoryModel(body: unknown, path: string): Record<string, unknown> {
  if (!record(body) || body.type !== "directory" || body.path !== path) throw new PersonalWorkspaceError("invalid_response");
  return body;
}
export async function listPersonalDirectory(configuration: WorkspaceConfiguration, path: string, signal: AbortSignal): Promise<PersonalEntry[]> {
  const body = directoryModel(await contents(configuration, path, signal, { query: "?content=1" }), path);
  if (!Array.isArray(body.content) || body.content.length > 1000) throw new PersonalWorkspaceError("invalid_response");
  const seen = new Set<string>(); const entries: PersonalEntry[] = [];
  for (const row of body.content) {
    if (!record(row) || typeof row.path !== "string" || typeof row.name !== "string" || row.name !== row.path.split("/").at(-1) || parentPath(row.path) !== path || seen.has(row.path)) throw new PersonalWorkspaceError("invalid_response");
    seen.add(row.path);
    if (row.name.startsWith(".")) continue;
    if (!safePath(row.path) || !["file", "notebook", "directory"].includes(String(row.type)) || typeof row.writable !== "boolean" || row.size !== null && row.size !== undefined && (!Number.isSafeInteger(row.size) || Number(row.size) < 0)) throw new PersonalWorkspaceError("invalid_response");
    entries.push({ name: row.name, path: row.path, type: row.type as PersonalEntry["type"], writable: row.writable, size: typeof row.size === "number" ? row.size : null });
  }
  return entries.sort((a, b) => Number(b.type === "directory") - Number(a.type === "directory") || a.name.localeCompare(b.name));
}
export async function listPersonalSkills(configuration: WorkspaceConfiguration, signal: AbortSignal): Promise<PersonalEntry[]> {
  try { return (await listPersonalDirectory(configuration, "skills", signal)).filter(file => file.type === "file" && file.name.endsWith(".md")); }
  catch (error) { if (error instanceof PersonalWorkspaceError && error.code === "not_found") return []; throw error; }
}
export async function listPersonalFiles(configuration: WorkspaceConfiguration, signal: AbortSignal): Promise<{ files: PersonalEntry[]; limited: boolean }> {
  const queue = [""]; const files: PersonalEntry[] = []; let requests = 0, limited = false;
  while (queue.length && requests < 64 && files.length < 500) {
    const path = queue.shift()!; requests++;
    for (const entry of await listPersonalDirectory(configuration, path, signal)) {
      if (entry.type === "directory") {
        if (entry.path.split("/").length < 8 && queue.length + requests < 64) queue.push(entry.path); else limited = true;
      } else if (files.length < 500) files.push(entry); else limited = true;
    }
  }
  return { files: files.sort((a, b) => a.path.localeCompare(b.path)), limited: limited || queue.length > 0 };
}
export async function readPersonalFile(configuration: WorkspaceConfiguration, path: string, signal: AbortSignal, limit = MAX_PERSONAL_BYTES): Promise<{ bytes: Uint8Array<ArrayBuffer>; mimeType: string }> {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > MAX_PERSONAL_BYTES) throw new PersonalWorkspaceError("too_large");
  checkedPath(path);
  const encodedLimit = Math.ceil(limit / 3) * 4;
  const lineLimit = Math.ceil(encodedLimit / 76);
  // Jupyter wraps at 76 characters; each CRLF occupies four escaped JSON bytes.
  // Jupyter otherwise returns a parsed notebook model, ignoring base64. Force
  // the file model so preview and download retain the saved JSON bytes.
  const query = `?content=1&format=base64${/\.ipynb$/i.test(path) ? "&type=file" : ""}`;
  const body = await contents(configuration, path, signal, { query, limit: encodedLimit + lineLimit * 4 + 16384 });
  if (!record(body) || body.type !== "file" || body.path !== path || body.format !== "base64" || typeof body.content !== "string" || body.size !== null && body.size !== undefined && (!Number.isSafeInteger(body.size) || Number(body.size) < 0)) throw new PersonalWorkspaceError("invalid_response");
  if (Number(body.size ?? 0) > limit || body.content.length > encodedLimit + lineLimit * 2) throw new PersonalWorkspaceError("too_large");
  const lines = body.content.split(/\r?\n/);
  if (lines.length > 1) {
    if (lines.at(-1) === "") lines.pop();
    if (lines.some((line, index) => line.length < 1 || line.length > 76 || index < lines.length - 1 && line.length !== 76)) throw new PersonalWorkspaceError("invalid_response");
  }
  const content = lines.join("");
  if (content.length > encodedLimit) throw new PersonalWorkspaceError("too_large");
  // Flat alphabet and padding checks avoid regexp stack overflow on 10 MB files.
  if (content.length % 4 !== 0 || /[^A-Za-z0-9+/]/.test(content.replace(/={1,2}$/, ""))) throw new PersonalWorkspaceError("invalid_response");
  let decoded: string;
  try { decoded = atob(content); } catch { throw new PersonalWorkspaceError("invalid_response"); }
  cancelled(signal);
  if (decoded.length > limit) throw new PersonalWorkspaceError("too_large");
  const bytes = Uint8Array.from(decoded, character => character.charCodeAt(0));
  return { bytes, mimeType: typeof body.mimetype === "string" && body.mimetype.length <= 256 ? body.mimetype : "application/octet-stream" };
}
function skillPath(path: string): string {
  checkedPath(path);
  if (parentPath(path) !== "skills" || !path.endsWith(".md")) throw new PersonalWorkspaceError("invalid_path");
  return path;
}
export async function loadPersonalSkill(configuration: WorkspaceConfiguration, path: string, signal: AbortSignal): Promise<string> {
  const { bytes } = await readPersonalFile(configuration, skillPath(path), signal, MAX_SKILL_BYTES);
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { throw new PersonalWorkspaceError("invalid_response"); }
}
function confirmedFile(body: unknown, path?: string): string {
  if (!record(body) || body.type !== "file" || typeof body.path !== "string" || path && body.path !== path) throw new PersonalWorkspaceError("invalid_response");
  return skillPath(body.path);
}
export async function savePersonalSkill(configuration: WorkspaceConfiguration, input: { filename: string; content: string; path?: string }, signal: AbortSignal): Promise<string> {
  const target = skillPath(`skills/${input.filename}`);
  if (typeof input.content !== "string" || !input.content.trim()) throw new PersonalWorkspaceError("invalid_response");
  if (new TextEncoder().encode(input.content).byteLength > MAX_SKILL_BYTES) throw new PersonalWorkspaceError("too_large");
  if (input.path !== undefined && skillPath(input.path) !== target) throw new PersonalWorkspaceError("invalid_path");
  cancelled(signal); personalWorkspaceOrigin(configuration); xsrfCookie();
  let mayHaveWritten = false;
  try {
    if (!input.path) {
      const existing = await contents(configuration, "skills", signal, { query: "?content=0", missing: true });
      if (existing === null) { mayHaveWritten = true; directoryModel(await contents(configuration, "skills", signal, { method: "PUT", body: { type: "directory" } }), "skills"); }
      else directoryModel(existing, "skills");
      mayHaveWritten = true;
      const fresh = confirmedFile(await contents(configuration, "skills", signal, { method: "POST", body: { type: "file", ext: ".md" } }));
      if (fresh !== target) confirmedFile(await contents(configuration, fresh, signal, { method: "PATCH", body: { path: target } }), target);
    }
    mayHaveWritten = true;
    confirmedFile(await contents(configuration, target, signal, { method: "PUT", body: { type: "file", format: "text", content: input.content } }), target);
    return target;
  } catch (error) {
    cancelled(signal);
    if (error instanceof PersonalWorkspaceError && ["workspace_auth", "conflict", "invalid_path", "too_large"].includes(error.code)) throw error;
    throw mayHaveWritten ? new PersonalWorkspaceError("save_unconfirmed") : error;
  }
}
