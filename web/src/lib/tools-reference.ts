import { apiFetch } from "@/lib/api-client";
import type { ToolInfo } from "craft-api/api/types";

export type ToolChannel = ToolInfo["availableIn"][number];
export type ToolFilter = ToolChannel | "all";
type ErrorCode = "unauthenticated" | "unavailable" | "invalid_response";
const channels = ["api", "mcp", "chat", "read_only_query"] as const;
const effects = ["NONE", "DATA", "DOCUMENT", "EXTERNAL", "FINAL"] as const;

export class ToolReferenceError extends Error {
  readonly code: ErrorCode;
  constructor(code: ErrorCode) {
    super(code === "unauthenticated" ? "Sign in to view tools." : "Could not load tool reference.");
    this.name = "ToolReferenceError";
    this.code = code;
  }
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function validTool(value: unknown): value is ToolInfo {
  if (!object(value)) return false;
  return typeof value.name === "string" && /^[a-z0-9_]{1,64}$/.test(value.name)
    && typeof value.operation === "string" && typeof value.description === "string"
    && typeof value.route === "string" && typeof value.needsApproval === "boolean"
    && typeof value.effects === "string" && effects.some(effect => effect === value.effects)
    && object(value.inputSchema) && (value.outputSchema === null || object(value.outputSchema))
    && Array.isArray(value.availableIn) && value.availableIn.every(channel => channels.some(known => known === channel));
}

function checkAbort(signal: AbortSignal) {
  if (signal.aborted) throw new DOMException("Reference request cancelled.", "AbortError");
}

export async function fetchToolReference(signal: AbortSignal): Promise<ToolInfo[]> {
  checkAbort(signal);
  let response: Response;
  try {
    response = await apiFetch("/api/tools", { method: "GET", credentials: "same-origin", signal });
  } catch {
    checkAbort(signal);
    throw new ToolReferenceError("unavailable");
  }
  checkAbort(signal);
  if (response.status === 401) throw new ToolReferenceError("unauthenticated");
  if (!response.ok) throw new ToolReferenceError("unavailable");
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    checkAbort(signal);
    throw new ToolReferenceError("invalid_response");
  }
  checkAbort(signal);
  if (!Array.isArray(body) || !body.every(validTool) || new Set(body.map(tool => tool.name)).size !== body.length) {
    throw new ToolReferenceError("invalid_response");
  }
  return body;
}

export function filterTools(tools: ToolInfo[], query: string, channel: ToolFilter): ToolInfo[] {
  const term = query.trim().toLocaleLowerCase();
  return tools.filter(tool => (channel === "all" || tool.availableIn.includes(channel))
    && `${tool.name} ${tool.description}`.toLocaleLowerCase().includes(term))
    .sort((left, right) => left.name.localeCompare(right.name));
}
