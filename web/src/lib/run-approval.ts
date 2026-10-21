import { apiFetch } from "@/lib/api-client";
import type { Approval, ApprovalDetail, ApprovalEvent, ApprovalStatus, RunRecovery } from "craft-api/api/types";
import { parseRunRecovery, type RunRecoveryView } from "@/lib/run-recovery";

const unavailable = "Approval unavailable.";
const statuses = ["pending", "approved", "rejected", "executed", "failed"] as const;
const eventKinds = ["requested", "approved", "rejected", "executed", "failed"] as const;
const summaryFields = ["id", "conversationId", "runId", "tool", "summary", "status", "requestedAt"] as const;
const detailFields = [...summaryFields, "arguments", "evidence", "reasoning", "retrievalPath", "events"] as const;
const has = (value: object, field: string) => Object.prototype.hasOwnProperty.call(value, field);
const isRecord = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object"
  && !Array.isArray(value) && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
const isUUID = (value: unknown): value is string => typeof value === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const isTimestamp = (value: unknown): value is string => typeof value === "string"
  && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const isStatus = (value: unknown): value is ApprovalStatus => typeof value === "string" && statuses.some(status => status === value);

function parseSummary(value: unknown): Approval {
  if (!isRecord(value) || !summaryFields.every(field => has(value, field))) throw new Error(unavailable);
  const { id, conversationId, runId, tool, summary, status, requestedAt } = value;
  if (!isUUID(id) || !isUUID(conversationId) || !isUUID(runId) || typeof tool !== "string"
    || typeof summary !== "string" || !isStatus(status) || !isTimestamp(requestedAt)) throw new Error(unavailable);
  return { id, conversationId, runId, tool, summary, status, requestedAt };
}

// Clone JSON as data, refusing cycles and values that JSON would silently drop.
function jsonValue(value: unknown, ancestors = new Set<object>()): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (!Array.isArray(value) && !isRecord(value)) throw new Error(unavailable);
  if (ancestors.has(value)) throw new Error(unavailable);
  ancestors.add(value);
  try {
    if (Array.isArray(value)) return Array.from(value, item => jsonValue(item, ancestors));
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, jsonValue(item, ancestors)]));
  } finally { ancestors.delete(value); }
}
function jsonRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(unavailable);
  return jsonValue(value) as Record<string, unknown>;
}
function jsonRecords(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) throw new Error(unavailable);
  return Array.from(value, jsonRecord);
}
function parseEvent(value: unknown): ApprovalEvent {
  if (!isRecord(value) || !["kind", "actorId", "at"].every(field => has(value, field))) throw new Error(unavailable);
  const { kind, actorId, at } = value;
  if (typeof kind !== "string" || !eventKinds.some(eventKind => eventKind === kind) || (actorId !== null && !isUUID(actorId)) || !isTimestamp(at)
    || (has(value, "note") && value.note !== null && typeof value.note !== "string")) throw new Error(unavailable);
  return { kind: kind as ApprovalEvent["kind"], actorId, at, ...(has(value, "note") ? { note: value.note as string | null } : {}) };
}

export function parseApprovalDetail(value: unknown): ApprovalDetail {
  try {
    if (!isRecord(value) || !detailFields.every(field => has(value, field))
      || typeof value.reasoning !== "string" || !Array.isArray(value.events)) throw new Error(unavailable);
    return { ...parseSummary(value), arguments: jsonRecord(value.arguments), evidence: jsonRecords(value.evidence),
      reasoning: value.reasoning, retrievalPath: jsonRecords(value.retrievalPath), events: Array.from(value.events, parseEvent) };
  } catch { throw new Error(unavailable); }
}

export interface RunApprovalView {
  phase: "idle" | "loading" | "ready" | "error";
  data: ApprovalDetail | null;
  busy: "decision" | "resume" | null;
  canDecide: boolean;
  canResume: boolean;
  message?: string;
}
interface RunApprovalCallbacks {
  onAuthInvalid: () => void;
  onRecoveryRefresh: () => void | Promise<void>;
  onResume: (runId: string, approvalId: string) => void | Promise<unknown>;
}
const emptyView = (): RunApprovalView => ({ phase: "idle", data: null, busy: null, canDecide: false, canResume: false });

// Reads and actions share lifetime/request tickets. Abort alone cannot stop late JSON.
export class RunApprovalController {
  private readonly sessionId: string;
  private readonly callbacks: RunApprovalCallbacks;
  private readonly fetcher: typeof fetch;
  private readonly listeners = new Set<() => void>();
  private active = false;
  private lifetime = 0;
  private request = 0;
  private controller: AbortController | null = null;
  private recovery: RunRecovery | null = null;
  private contextKey: string | null = null;
  private consumedKey: string | null = null;
  // A decision made here resumes the run once its recorded status loads, without a second click.
  private resumeAfterDecision: string | null = null;
  private snapshot = emptyView();

  constructor(sessionId: string, callbacks: RunApprovalCallbacks, fetcher: typeof fetch = apiFetch) {
    this.sessionId = sessionId;
    this.callbacks = callbacks;
    this.fetcher = fetcher;
  }
  getSnapshot = (): RunApprovalView => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  activate = () => {
    if (this.active) return;
    this.active = true;
    ++this.lifetime;
    if (this.recovery) void this.refresh();
  };
  release = () => {
    this.active = false;
    ++this.lifetime;
    this.invalidate();
    this.recovery = null;
    this.contextKey = null;
    this.consumedKey = null;
    this.resumeAfterDecision = null;
    this.snapshot = emptyView();
  };
  setRecovery = (view: RunRecoveryView) => {
    let data: RunRecovery | null = null;
    try {
      if (view?.phase === "ready" && isUUID(this.sessionId)) {
        const parsed = parseRunRecovery(view.data);
        if (parsed.status === "paused" && parsed.runId !== null && parsed.approvalId !== null) data = parsed;
      }
    } catch { /* Unknown metadata cannot authorize an approval action. */ }
    const key = data ? JSON.stringify(data) : null;
    if (key !== null && key === this.contextKey) return;
    this.invalidate();
    this.recovery = data;
    this.contextKey = key;
    this.consumedKey = null;
    this.publish(emptyView());
    if (this.active && data) void this.refresh();
  };
  refresh = async (): Promise<void> => {
    if (!this.active || !this.recovery || this.snapshot.busy !== null) return;
    const recovery = this.recovery;
    const operation = this.begin();
    this.publish({ ...emptyView(), phase: "loading" });
    try {
      if (!operation.current()) return;
      const fetcher = this.fetcher;
      const response = await fetcher(`/api/approvals/${encodeURIComponent(recovery.approvalId!)}`,
        { method: "GET", credentials: "same-origin", signal: operation.signal });
      if (!operation.current()) return;
      if (!this.checkResponse(response, operation.current)) return;
      const body: unknown = await response.json();
      if (!operation.current()) return;
      const data = parseApprovalDetail(body);
      if (!this.matches(data, recovery) || data.status !== recovery.approvalStatus) throw new Error(unavailable);
      if (!operation.current()) return;
      this.publish(this.readyView(data, recovery));
      if (this.resumeAfterDecision === data.id) {
        this.resumeAfterDecision = null;
        if (this.snapshot.canResume) void this.resume();
      }
    } catch { if (operation.current()) this.publishError(); }
  };
  decide = async (approve: boolean): Promise<void> => {
    if (typeof approve !== "boolean" || !this.active || !this.snapshot.canDecide || this.snapshot.busy !== null
      || !this.snapshot.data || !this.recovery) return;
    const data = this.snapshot.data;
    const recovery = this.recovery;
    const operation = this.begin();
    this.publish({ ...this.snapshot, busy: "decision", canDecide: false, canResume: false });
    try {
      if (!operation.current()) return;
      const fetcher = this.fetcher;
      const response = await fetcher(`/api/approvals/${encodeURIComponent(data.id)}/decision`, {
        method: "POST", credentials: "same-origin", signal: operation.signal,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve }),
      });
      if (!operation.current()) return;
      if (!this.checkResponse(response, operation.current)) return;
      const body: unknown = await response.json();
      if (!operation.current()) return;
      const decided = parseSummary(body);
      if (!this.matches(decided, recovery) || decided.status !== (approve ? "approved" : "rejected")) throw new Error(unavailable);
      this.consumedKey = this.contextKey;
      this.resumeAfterDecision = data.id;
      this.publish(emptyView());
      if (operation.current()) void this.callbacks.onRecoveryRefresh();
    } catch { if (operation.current()) this.publishError(); }
  };
  resume = async (): Promise<void> => {
    if (!this.active || !this.snapshot.canResume || this.snapshot.busy !== null || !this.snapshot.data || !this.recovery) return;
    const data = this.snapshot.data;
    const operation = this.begin();
    this.publish({ ...this.snapshot, busy: "resume", canDecide: false, canResume: false });
    try {
      if (!operation.current()) return;
      await this.callbacks.onResume(data.runId, data.id);
      if (!operation.current()) return;
      this.consumedKey = this.contextKey;
      this.publish({ ...this.snapshot, busy: null, canDecide: false, canResume: false });
    } catch {
      if (!operation.current()) return;
      this.publishError();
      if (operation.current()) void this.callbacks.onRecoveryRefresh();
    }
  };
  private matches(data: Approval, recovery: RunRecovery): boolean {
    return data.id === recovery.approvalId && data.runId === recovery.runId && data.conversationId === this.sessionId;
  }
  private readyView(data: ApprovalDetail, recovery: RunRecovery): RunApprovalView {
    const available = this.consumedKey !== this.contextKey;
    return { phase: "ready", data, busy: null, canDecide: available && data.status === "pending",
      canResume: available && recovery.canResume && (data.status === "approved" || data.status === "rejected") };
  }
  private invalidate() {
    ++this.request;
    this.controller?.abort();
    this.controller = null;
  }
  private begin() {
    this.invalidate();
    const lifetime = this.lifetime;
    const ticket = this.request;
    const controller = new AbortController();
    this.controller = controller;
    return { signal: controller.signal, current: () => this.active && lifetime === this.lifetime
      && ticket === this.request && !controller.signal.aborted };
  }
  private checkResponse(response: Response, current: () => boolean): boolean {
    if (response.ok) return true;
    this.publishError();
    if (response.status === 401 && current()) this.callbacks.onAuthInvalid();
    return false;
  }
  private publishError() { this.publish({ ...emptyView(), phase: "error", message: unavailable }); }
  private publish(view: RunApprovalView) {
    this.snapshot = view;
    for (const listener of this.listeners) listener();
  }
}
