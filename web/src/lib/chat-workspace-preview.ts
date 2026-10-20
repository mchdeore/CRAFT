import type { DocumentFormat } from "@/components/citations/preview/renderer-contract";
import { personalFileFormat } from "@/lib/personal-preview";
import { personalAbsolutePath } from "@/lib/personal-workspace";

// Parse the raw href before URL normalization can hide traversal. Decode each
// component once; a decoded separator, percent or query delimiter is invalid.
export function personalWorkspaceLinkPath(href: string | null | undefined): string | null {
  if (typeof href !== "string" || !href.startsWith("/workspace/") || /[?#]/.test(href) || [...href].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) return null;
  try {
    const parts = href.slice("/workspace/".length).split("/").map(part => decodeURIComponent(part));
    if (parts.some(part => /[/\\]/.test(part))) return null;
    const path = parts.join("/");
    personalAbsolutePath(path);
    return path;
  } catch { return null; }
}

export interface NotebookPreviewCell {
  type: "code" | "markdown" | "raw";
  source: string;
  outputs: string[];
  unsupportedOutputs: number;
  truncated: boolean;
}
export type PersonalChatPreview =
  | { kind: "document"; format: DocumentFormat }
  | { kind: "code"; language: "Python"; text: string; truncated: boolean }
  | { kind: "notebook"; cells: NotebookPreviewCell[]; truncated: boolean }
  | { kind: "unsupported" };

const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
function notebookText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && value.every(part => typeof part === "string")) return value.join("");
  throw new Error("Notebook preview is unavailable: invalid cell text.");
}
function utf8(bytes: Uint8Array): string {
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { throw new Error("Code preview is unavailable: this file is not valid UTF-8."); }
}
function notebookPreview(bytes: Uint8Array): PersonalChatPreview {
  let notebook: unknown;
  try { notebook = JSON.parse(utf8(bytes)) as unknown; }
  catch { throw new Error("Notebook preview is unavailable: invalid notebook JSON."); }
  if (!record(notebook) || notebook.nbformat !== 4 || !Array.isArray(notebook.cells)) throw new Error("Notebook preview is unavailable: expected a version 4 notebook.");
  const cells: NotebookPreviewCell[] = [];
  let remaining = 200000, truncated = notebook.cells.length > 200;
  for (const row of notebook.cells.slice(0, 200)) {
    if (remaining === 0) { truncated = true; break; }
    if (!record(row) || !["code", "markdown", "raw"].includes(String(row.cell_type))) throw new Error("Notebook preview is unavailable: invalid notebook cell.");
    const cell: NotebookPreviewCell = { type: row.cell_type as NotebookPreviewCell["type"], source: "", outputs: [], unsupportedOutputs: 0, truncated: false };
    const bounded = (text: string, limit: number) => {
      const value = text.slice(0, Math.min(limit, remaining));
      remaining -= value.length;
      if (value.length < text.length) { cell.truncated = true; truncated = true; }
      return value;
    };
    cell.source = bounded(notebookText(row.source), 20000);
    if (cell.type === "code") {
      if (!Array.isArray(row.outputs)) throw new Error("Notebook preview is unavailable: invalid cell outputs.");
      if (row.outputs.length > 20) { cell.truncated = true; truncated = true; }
      for (const output of row.outputs.slice(0, 20)) {
        if (remaining === 0) { cell.truncated = true; truncated = true; break; }
        if (!record(output)) throw new Error("Notebook preview is unavailable: invalid cell output.");
        let text: string | undefined;
        if (output.output_type === "stream") text = notebookText(output.text);
        else if (output.output_type === "error") {
          if (Array.isArray(output.traceback) && output.traceback.length > 0 && output.traceback.every(line => typeof line === "string")) text = output.traceback.join("\n");
          else if (typeof output.ename === "string" && typeof output.evalue === "string") text = `${output.ename}: ${output.evalue}`;
          else throw new Error("Notebook preview is unavailable: invalid error output.");
        } else if (["execute_result", "display_data"].includes(String(output.output_type)) && record(output.data)) {
          if (Object.hasOwn(output.data, "text/plain")) text = notebookText(output.data["text/plain"]);
          if (Object.keys(output.data).some(key => key !== "text/plain") || text === undefined) cell.unsupportedOutputs++;
        } else cell.unsupportedOutputs++;
        if (text !== undefined) cell.outputs.push(bounded(text, 10000));
      }
    }
    cells.push(cell);
  }
  return { kind: "notebook", cells, truncated };
}

export function personalChatPreview(path: string, bytes: Uint8Array): PersonalChatPreview {
  personalAbsolutePath(path);
  const extension = path.split(".").at(-1)?.toLowerCase();
  if (extension === "ipynb") return notebookPreview(bytes);
  if (extension === "py") {
    const text = utf8(bytes);
    return { kind: "code", language: "Python", text: text.slice(0, 100000), truncated: text.length > 100000 };
  }
  const format = personalFileFormat(path);
  return format ? { kind: "document", format } : { kind: "unsupported" };
}
