import { apiFetch } from "@/lib/api-client";
import type { RunRecovery } from "craft-api/api/types";

const unavailable = "Run status unavailable.";
const runStatuses = ["none", "running", "paused", "finished", "failed", "cancelled"] as const;
const approvalStatuses = ["pending", "approved", "rejected", "executed", "failed"] as const;
const requiredFields = ["status", "runId", "startedAt", "endedAt", "approvalId", "approvalStatus", "canResume"] as const;

function isOneOf<T extends string>(value: unknown, choices: readonly T[]): value is T {
  return typeof value === "string" && choices.some((choice) => choice === value);
}

function isNullableUUID(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value));
}

function isNullableTimestamp(value: unknown): value is string | null {
  return value === null || (typeof value === "string"
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value)));
}

// Project only the documented metadata, even if a response contains other keys.
export function parseRunRecovery(value: unknown): RunRecovery {
  if (value === null || typeof value !== "object" || Array.isArray(value)
    || !requiredFields.every((field) => Object.prototype.hasOwnProperty.call(value, field))) {
    throw new Error(unavailable);
  }
  const { status, runId, startedAt, endedAt, approvalId, approvalStatus, canResume } = value as Record<string, unknown>;
  if (!isOneOf(status, runStatuses) || !isNullableUUID(runId)
    || !isNullableTimestamp(startedAt) || !isNullableTimestamp(endedAt)
    || !isNullableUUID(approvalId)
    || (approvalStatus !== null && !isOneOf(approvalStatus, approvalStatuses))
    || typeof canResume !== "boolean") {
    throw new Error(unavailable);
  }
  if ((approvalId === null) !== (approvalStatus === null)
    || (status !== "paused" && approvalId !== null)
    || (canResume && (status !== "paused" || approvalId === null
      || (approvalStatus !== "approved" && approvalStatus !== "rejected")))
    || (status === "none" && (runId !== null || startedAt !== null || endedAt !== null))) {
    throw new Error(unavailable);
  }
  return { status, runId, startedAt, endedAt, approvalId, approvalStatus, canResume };
}

export interface RunRecoveryView {
  phase: "idle" | "loading" | "ready" | "error";
  data: RunRecovery | null;
  message?: string;
}

interface RunRecoveryCallbacks {
  onAuthInvalid: () => void;
  onSessionInvalid: () => void | Promise<void>;
}

// Each request belongs to one active conversation lifetime. Abort is backed by
// tickets because a response or its JSON can finish after cancellation.
export class RunRecoveryController {
  private active = false;
  private lifetime = 0;
  private request = 0;
  private controller: AbortController | null = null;
  private snapshot: RunRecoveryView = { phase: "idle", data: null };
  private readonly listeners = new Set<() => void>();
  private readonly sessionId: string;
  private readonly callbacks: RunRecoveryCallbacks;
  private readonly fetcher: typeof fetch;

  constructor(
    sessionId: string,
    callbacks: RunRecoveryCallbacks,
    fetcher: typeof fetch = apiFetch,
  ) {
    this.sessionId = sessionId;
    this.callbacks = callbacks;
    this.fetcher = fetcher;
  }

  getSnapshot = (): RunRecoveryView => this.snapshot;

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  activate = () => {
    if (this.active) return;
    this.active = true;
    ++this.lifetime;
  };

  release = () => {
    this.active = false;
    ++this.lifetime;
    ++this.request;
    this.controller?.abort();
    this.controller = null;
    this.snapshot = { phase: "idle", data: null };
  };

  refresh = async (): Promise<void> => {
    if (!this.active) return;
    const lifetime = this.lifetime;
    const ticket = ++this.request;
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    const current = () => this.active && lifetime === this.lifetime
      && ticket === this.request && !controller.signal.aborted;
    this.publish({ phase: "loading", data: null });
    try {
      if (!current()) return;
      const fetcher = this.fetcher;
      const response = await fetcher(
        `/api/conversations/${encodeURIComponent(this.sessionId)}/run`,
        { method: "GET", credentials: "same-origin", signal: controller.signal },
      );
      if (!current()) return;
      if (response.status === 401 || response.status === 404) {
        this.publish({ phase: "error", data: null, message: unavailable });
        if (!current()) return;
        if (response.status === 401) this.callbacks.onAuthInvalid();
        else void this.callbacks.onSessionInvalid();
        return;
      }
      if (!response.ok) throw new Error(unavailable);
      if (!current()) return;
      const body: unknown = await response.json();
      if (!current()) return;
      const data = parseRunRecovery(body);
      if (!current()) return;
      this.publish({ phase: "ready", data });
    } catch {
      if (current()) this.publish({ phase: "error", data: null, message: unavailable });
    } finally {
      if (current()) this.controller = null;
    }
  };

  private publish(snapshot: RunRecoveryView) {
    if (!this.active) return;
    this.snapshot = snapshot;
    for (const listener of this.listeners) {
      if (!this.active || this.snapshot !== snapshot) break;
      listener();
    }
  }
}
