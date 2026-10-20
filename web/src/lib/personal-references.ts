import type { DirectiveFormatter, TriggerAdapter, TriggerMatcher } from "@assistant-ui/react";
import { MAX_SKILL_BYTES, personalAbsolutePath, type PersonalEntry } from "@/lib/personal-workspace";

export type PersonalReferenceKind = "skills" | "files";
// Name/path searches may contain spaces, but never cross a line or quoted path.
// Keep the installed replacement and Escape contract: [trigger, caret).
export const personalReferenceMatcher: TriggerMatcher = (text, triggerChar, cursorPosition) => {
  if ((triggerChar !== "@" && triggerChar !== "/") || !Number.isInteger(cursorPosition) || cursorPosition <= 0 || cursorPosition > text.length) return null;
  const atCaret = text[cursorPosition];
  if ((atCaret === "@" || atCaret === "/") && /\s/u.test(text[cursorPosition - 1]!)) return null;
  const beforeCaret = text.slice(0, cursorPosition);
  for (let offset = beforeCaret.length - 1; offset >= 0; offset--) {
    const char = beforeCaret[offset]!;
    if (/[\r\n"`]/u.test(char)) return null;
    if (char !== "@" && char !== "/") continue;
    if (offset > 0 && !/\s/u.test(beforeCaret[offset - 1]!)) continue;
    if (char !== triggerChar) return null; // Only the most recent picker is active.
    const query = beforeCaret.slice(offset + 1);
    if (char === "/" && query.startsWith("workspace/")) return null; // Inserted literal paths stay closed.
    return { query, offset, endOffset: cursorPosition };
  }
  return null;
};
export function personalReferenceAdapter(files: PersonalEntry[], kind: PersonalReferenceKind): TriggerAdapter {
  const items = files.filter(file => file.type !== "directory" && (kind !== "skills" || file.type === "file" && /^skills\/[^/]+\.md$/.test(file.path))).flatMap(file => {
    try { return [{ id: file.path, type: "personal-file", label: file.name, description: personalAbsolutePath(file.path) }]; }
    catch { return []; }
  });
  return {
    categories: () => [], categoryItems: () => [],
    search: query => {
      const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
      return items.filter(item => words.every(word => `${item.label} ${item.description}`.toLocaleLowerCase().includes(word)));
    },
  };
}
export function personalPathText(path: string): string {
  const absolute = personalAbsolutePath(path);
  return /\s|["`]/.test(absolute) ? JSON.stringify(absolute) : absolute;
}
// The stock textarea trigger inserts this literal text through composer.setText.
// Parsing stays plain text; no attachment, skill ID or run-config fields are added.
export const personalPathFormatter: DirectiveFormatter = {
  serialize: item => personalPathText(item.id),
  parse: text => [{ kind: "text", text }],
};
export function insertPersonalReferenceTrigger(content: string, kind: PersonalReferenceKind, start: number, end = start): { content: string; caret: number } {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || end > content.length) throw new Error("Invalid reference selection");
  const before = content.slice(0, start), after = content.slice(end);
  const prefix = before && !/\s$/.test(before) ? " " : "";
  const suffix = after && !/^\s/.test(after) ? " " : "";
  const trigger = kind === "skills" ? "/" : "@";
  return { content: before + prefix + trigger + suffix + after, caret: before.length + prefix.length + trigger.length };
}
export function insertPersonalDocumentText(content: string, addition: string, start: number, end = start): { content: string; caret: number } {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || end > content.length) throw new Error("Invalid document selection");
  const before = content.slice(0, start), after = content.slice(end);
  const prefix = before && !/\s$/.test(before) && !/^\s/.test(addition) ? " " : "";
  const suffix = after && !/^\s/.test(after) && !/\s$/.test(addition) ? " " : "";
  const next = before + prefix + addition + suffix + after;
  if (new TextEncoder().encode(next).byteLength > MAX_SKILL_BYTES) throw new Error("Skill document too large");
  return { content: next, caret: before.length + prefix.length + addition.length };
}
