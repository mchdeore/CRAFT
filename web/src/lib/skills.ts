import { apiFetch } from "@/lib/api-client";
import type { SaveSkill, Skill, SkillCatalog, SkillSummary, ToolInfo } from "craft-api/api/types";

type ErrorCode = "unauthenticated" | "conflict" | "not_found" | "invalid_input" | "invalid_response" | "unavailable";
export class SkillError extends Error {
  readonly code: ErrorCode;
  constructor(code: ErrorCode) {
    const messages: Record<ErrorCode, string> = {
      unauthenticated: "Sign in to view skills.",
      conflict: "Save conflicted. Your edits are still here. Reload the saved version before saving again.",
      not_found: "This saved skill is no longer available.",
      invalid_input: "Check the skill fields and registered tool references.",
      invalid_response: "Could not read the saved skill response.",
      unavailable: "Could not reach the skills service. Try again.",
    };
    super(messages[code]); this.name = "SkillError"; this.code = code;
  }
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const toolName = /^[a-z0-9_]{1,64}$/;
export type SkillSelection = Pick<Skill, "id" | "revision" | "name">;
export function copySkillSelection(value: SkillSelection): SkillSelection {
  if (!object(value) || typeof value.id !== "string" || !uuid.test(value.id)
    || !Number.isInteger(value.revision) || value.revision < 1 || value.revision > 2147483647
    || typeof value.name !== "string" || value.name.length === 0 || [...value.name].length > 200) throw new SkillError("invalid_input");
  return { id: value.id, revision: value.revision, name: value.name };
}

function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value); }
function strings(value: unknown): value is string[] { return Array.isArray(value) && value.every(item => typeof item === "string"); }
function names(value: unknown): value is string[] { return strings(value) && value.length <= 100 && new Set(value).size === value.length && value.every(name => toolName.test(name)); }
function validSummary(value: unknown): value is SkillSummary {
  return object(value) && typeof value.id === "string" && uuid.test(value.id) &&
    Number.isInteger(value.revision) && Number(value.revision) > 0 && Number(value.revision) <= 2147483647 &&
    typeof value.name === "string" && value.name.length > 0 && [...value.name].length <= 200 &&
    typeof value.description === "string" && [...value.description].length <= 2000 &&
    names(value.toolNames) && names(value.unavailableTools) && value.unavailableTools.every(name => (value.toolNames as string[]).includes(name)) &&
    typeof value.createdAt === "string" && Number.isFinite(Date.parse(value.createdAt)) &&
    typeof value.updatedAt === "string" && Number.isFinite(Date.parse(value.updatedAt));
}
function validSkill(value: unknown): value is Skill {
  return validSummary(value) && object(value) && typeof value.instructions === "string" &&
    value.instructions.length > 0 && [...value.instructions].length <= 100000 && strings(value.exampleInputs) &&
    value.exampleInputs.length <= 20 && value.exampleInputs.every(item => [...item].length <= 2000);
}
function aborted(signal: AbortSignal) { if (signal.aborted) throw new DOMException("Request cancelled.", "AbortError"); }
async function request(url: string, signal: AbortSignal, options: RequestInit = {}): Promise<unknown> {
  aborted(signal);
  let response: Response;
  try { response = await apiFetch(url, { method: "GET", ...options, credentials: "same-origin", signal }); }
  catch { aborted(signal); throw new SkillError("unavailable"); }
  aborted(signal);
  if (!response.ok) {
    const codes: Record<number, ErrorCode> = { 401: "unauthenticated", 409: "conflict", 404: "not_found", 400: "invalid_input" };
    throw new SkillError(codes[response.status] ?? "unavailable");
  }
  let value: unknown;
  try { value = await response.json(); } catch { aborted(signal); throw new SkillError("invalid_response"); }
  aborted(signal); return value;
}
function identifier(id: string) { if (!uuid.test(id)) throw new SkillError("invalid_input"); return encodeURIComponent(id); }
export async function fetchSkills(signal: AbortSignal, after?: string): Promise<SkillCatalog> {
  const value = await request(`/api/skills${after ? `?after=${identifier(after)}` : ""}`, signal);
  if (!object(value) || !Array.isArray(value.items) || !value.items.every(validSummary) ||
    new Set(value.items.map(item => item.id)).size !== value.items.length ||
    !(value.nextCursor === null || typeof value.nextCursor === "string" && uuid.test(value.nextCursor) && value.items.length > 0 && value.nextCursor === value.items[value.items.length - 1].id)) throw new SkillError("invalid_response");
  return value as unknown as SkillCatalog;
}
export async function fetchSkill(id: string, signal: AbortSignal, revision?: number): Promise<Skill> {
  const path = identifier(id);
  if (revision !== undefined && (!Number.isInteger(revision) || revision < 1 || revision > 2147483647)) throw new SkillError("invalid_input");
  const value = await request(`/api/skills/${path}${revision === undefined ? "" : `/versions?revision=${revision}`}`, signal);
  if (!validSkill(value) || value.id.toLowerCase() !== id.toLowerCase() || revision !== undefined && value.revision !== revision) throw new SkillError("invalid_response");
  return value;
}
export async function saveSkill(input: SaveSkill, signal: AbortSignal): Promise<Skill> {
  const { id, ...body } = input;
  const value = await request(`/api/skills/${identifier(id)}`, signal, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!validSkill(value) || value.id.toLowerCase() !== id.toLowerCase() || value.revision !== input.expectedRevision + 1) throw new SkillError("invalid_response");
  return value;
}

export type SkillFields = Pick<SaveSkill, "name" | "description" | "instructions" | "exampleInputs" | "toolNames">;
export interface SkillDraft { id: string; expectedRevision: number; fields: SkillFields; attempt: SaveSkill | null; dirty: boolean }
function copyFields(fields: SkillFields): SkillFields { return { name: fields.name, description: fields.description, instructions: fields.instructions, exampleInputs: [...fields.exampleInputs], toolNames: [...fields.toolNames] }; }
function copyRequest(input: SaveSkill): SaveSkill { return { id: input.id, expectedRevision: input.expectedRevision, mutationId: input.mutationId, ...copyFields(input) }; }
export function newSkillDraft(id = crypto.randomUUID()): SkillDraft {
  return { id, expectedRevision: 0, fields: { name: "", description: "", instructions: "", exampleInputs: [], toolNames: [] }, attempt: null, dirty: true };
}
export function openSkillDraft(skill: Skill): SkillDraft { return { id: skill.id, expectedRevision: skill.revision, fields: copyFields(skill), attempt: null, dirty: false }; }
export function changeSkillDraft(draft: SkillDraft, changes: Partial<SkillFields>): SkillDraft { return { ...draft, fields: copyFields({ ...draft.fields, ...changes }), attempt: null, dirty: true }; }
export function prepareSkillSave(draft: SkillDraft, mutationId = crypto.randomUUID()): { draft: SkillDraft; request: SaveSkill } {
  const attempt = draft.attempt ?? { id: draft.id, expectedRevision: draft.expectedRevision, mutationId, ...copyFields(draft.fields) };
  return { draft: { ...draft, attempt: copyRequest(attempt) }, request: copyRequest(attempt) };
}

export function insertSkillText(fields: SkillFields, text: string, start: number, end = start): { fields: SkillFields; caret: number } {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || end > fields.instructions.length) throw new SkillError("invalid_input");
  const instructions = fields.instructions.slice(0, start) + text + fields.instructions.slice(end);
  if ([...instructions].length > 100000) throw new SkillError("invalid_input");
  return { fields: { ...copyFields(fields), instructions }, caret: start + text.length };
}

export function insertSkillTool(fields: SkillFields, tools: ToolInfo[], name: string, start: number, end = start): { fields: SkillFields; caret: number } {
  const references = [...new Set([...fields.toolNames, name])];
  if (!toolName.test(name) || !tools.some(tool => tool.name === name) || !names(references)) throw new SkillError("invalid_input");
  const before = fields.instructions.slice(0, start); const after = fields.instructions.slice(end);
  const text = `${before && !/\s$/.test(before) ? " " : ""}/${name}${after && !/^\s/.test(after) ? " " : ""}`;
  const inserted = insertSkillText(fields, text, start, end);
  inserted.fields.toolNames = references;
  return inserted;
}
