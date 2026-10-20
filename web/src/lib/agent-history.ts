import { apiFetch, apiURL } from "@/lib/api-client";
import type { SkillVersionRef } from "craft-api/api/types";
import { clearCitationCache } from "@/components/citations/citation-records";
import { HttpAgent, type AgentSubscriber, type RunAgentInput, type RunAgentParameters } from "@ag-ui/client";
import {
  ExportedMessageRepository,
  type ThreadHistoryAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { fromAgUiMessages } from "@assistant-ui/react-ag-ui";
import type { Conversation } from "craft-api/api/types";

export class AuthenticationRequiredError extends Error {
  constructor() { super("Sign in again to continue."); this.name = "AuthenticationRequiredError"; }
}

export class SessionNotFoundError extends Error {
  constructor() {
    super("Agent session was not found or has expired");
  }
}

// Loads the thread from the server's record of the session, so the server,
// not the browser, decides what the conversation contains. The server saves
// every message itself, so nothing is written back from here.
export function createSessionHistory(sessionId: string, signal?: AbortSignal): ThreadHistoryAdapter {
  return {
    load: async () => {
      const response = await apiFetch(
        `/api/conversations/${encodeURIComponent(sessionId)}`,
        { credentials: "same-origin", signal },
      );
      signal?.throwIfAborted();
      if (response.status === 401) throw new AuthenticationRequiredError();
      if (response.status === 404) throw new SessionNotFoundError();
      if (!response.ok) throw new Error("Could not load the conversation");

      const conversation = (await response.json()) as Conversation;
      signal?.throwIfAborted();
      if (!Array.isArray(conversation.messages)) throw new Error("Could not load the conversation");
      const messages = fromAgUiMessages(conversation.messages);
      return ExportedMessageRepository.fromArray(mergeAssistantTurns(messages));
    },
    append: async () => undefined,
  };
}

// The server records each step of a turn (tool calls, then the answer) as its
// own AG-UI message, while a live run shows the whole turn as one assistant
// message. Merge consecutive assistant messages so both look the same.
function mergeAssistantTurns(
  messages: readonly ThreadMessageLike[],
): ThreadMessageLike[] {
  const merged: ThreadMessageLike[] = [];

  for (const message of messages) {
    const previous = merged.at(-1);
    if (message.role === "assistant" && previous?.role === "assistant") {
      merged[merged.length - 1] = {
        ...previous,
        content: [...contentParts(previous), ...contentParts(message)],
        metadata: mergeAssistantMetadata(previous.metadata, message.metadata),
      };
    } else {
      merged.push(message);
    }
  }

  return merged;
}

// The pinned AG-UI SDK stores opaque provider state in metadata.custom.agui.
// Keep both records when combining assistant steps; never decode their values.
function mergeAssistantMetadata(
  previous: ThreadMessageLike["metadata"],
  incoming: ThreadMessageLike["metadata"],
): ThreadMessageLike["metadata"] {
  type AgUiMetadata = { opaqueReasoning?: readonly unknown[] };
  const before = previous?.custom?.agui as AgUiMetadata | undefined;
  const after = incoming?.custom?.agui as AgUiMetadata | undefined;
  const custom = { ...previous?.custom, ...incoming?.custom };
  if (before || after) {
    custom.agui = {
      ...before,
      ...after,
      opaqueReasoning: [...(before?.opaqueReasoning ?? []), ...(after?.opaqueReasoning ?? [])],
    };
  }
  return { ...previous, ...incoming, custom };
}

function contentParts(
  message: ThreadMessageLike,
): Exclude<ThreadMessageLike["content"], string> {
  return typeof message.content === "string"
    ? [{ type: "text", text: message.content }]
    : message.content;
}

function isCancellation(error: unknown): boolean {
  if (error instanceof Error && error.name === "AbortError") return true;
  const message = error instanceof Error ? error.message : error;
  return message === "Fetch is aborted" || message === "signal is aborted without reason" || message === "component unmounted";
}

const isUUID = (value: unknown): value is string => typeof value === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);

class PrivateHttpAgent extends HttpAgent {
  private readonly selectedSkill?: SkillVersionRef;
  constructor(options: ConstructorParameters<typeof HttpAgent>[0], skill?: SkillVersionRef) {
    super(options);
    if (skill !== undefined && (!isUUID(skill.id) || !Number.isInteger(skill.revision) || skill.revision < 1 || skill.revision > 2147483647)) throw new Error("Choose a saved skill revision.");
    this.selectedSkill = skill === undefined ? undefined : { id: skill.id, revision: skill.revision };
  }
  get hasSelectedSkill() { return this.selectedSkill !== undefined; }

  private readonly ownedResumes = new WeakMap<RunAgentParameters, string>();
  private readonly approvalInputs = new WeakMap<RunAgentInput, string>();
  private pendingRunId: string | null = null;
  private approvalBusy = false;

  get isApprovalResume() { return this.approvalBusy; }

  async resumeApproval(runId: string, approvalId: string, subscriber?: AgentSubscriber) {
    if (!isUUID(runId) || !isUUID(approvalId) || !isUUID(this.threadId)) throw new Error("Durable UUIDs are required to resume.");
    if (this.approvalBusy || this.isRunning) throw new Error("An agent run is already active.");
    const pending = this.pendingInterrupts;
    let interruptId = approvalId;
    if (pending.length > 0) {
      if (pending.length !== 1 || this.pendingRunId !== runId) throw new Error("The pending approval does not match this run.");
      const craft = pending[0].metadata?.craft;
      if (craft === null || typeof craft !== "object" || Array.isArray(craft)
        || (craft as Record<string, unknown>).approvalId !== approvalId || !pending[0].id) {
        throw new Error("The pending approval does not match this run.");
      }
      interruptId = pending[0].id;
    }
    // Native IDs stay inside SDK bookkeeping. Its initialization still checks
    // pending coverage and expiry before the request can reach HTTP.
    const parameters: RunAgentParameters = { runId, resume: [{ interruptId, status: "resolved" }] };
    this.ownedResumes.set(parameters, approvalId);
    this.approvalBusy = true;
    try { return await this.runAgent(parameters, subscriber); }
    finally { this.ownedResumes.delete(parameters); this.approvalBusy = false; }
  }

  protected requestInit(input: RunAgentInput): RequestInit {
    const init = super.requestInit(input);
    const approvalId = this.approvalInputs.get(input);
    if (approvalId === undefined) return this.selectedSkill && input.resume === undefined
      ? { ...init, body: JSON.stringify({ ...input, skill: this.selectedSkill }) } : init;
    // Adapt only the input object created for the owned explicit invocation.
    // Saved messages/state remain in the agent and initialized SDK subscribers.
    return { ...init, body: JSON.stringify({ threadId: input.threadId, runId: input.runId,
      tools: [], context: [], forwardedProps: {}, state: {}, messages: [], resume: { approvalId } }) };
  }
  protected prepareRunAgentInput(parameters?: RunAgentParameters): RunAgentInput {
    if (parameters?.resume !== undefined && (!parameters.runId || !isUUID(parameters.runId))) throw new Error("A durable UUID run ID is required to resume.");
    const input = super.prepareRunAgentInput(parameters);
    if (!isUUID(input.runId)) input.runId = crypto.randomUUID();
    const approvalId = parameters && this.ownedResumes.get(parameters);
    if (approvalId !== undefined) this.approvalInputs.set(input, approvalId);
    return input;
  }
  runAgent(parameters?: Parameters<HttpAgent["runAgent"]>[0], subscriber?: AgentSubscriber) {
    const controller = parameters?.abortController ?? new AbortController();
    let terminal = false;
    let failed = false;
    const guardedSubscriber: AgentSubscriber = {
      ...subscriber,
      onRunFinishedEvent: params => {
        if (params.event.runId !== params.input.runId || params.event.threadId !== params.input.threadId) return { stopPropagation: true };
        terminal = true;
        this.pendingRunId = params.event.outcome?.type === "interrupt" ? params.event.runId : null;
        return subscriber?.onRunFinishedEvent?.(params);
      },
      onRunErrorEvent: params => { terminal = true; return subscriber?.onRunErrorEvent?.(params); },
      onRunFailed: params => { failed = true; return subscriber?.onRunFailed?.(params); },
      onRunFinalized: params => {
        // The installed assistant-ui subscriber manufactures RUN_FINISHED on
        // EOF. Only forward finalization after a terminal event or cancellation.
        if (!terminal && !failed && !controller.signal.aborted) return;
        return subscriber?.onRunFinalized?.(params);
      },
    };
    const forwarded = { ...parameters, abortController: controller };
    const approvalId = parameters && this.ownedResumes.get(parameters);
    if (approvalId !== undefined) this.ownedResumes.set(forwarded, approvalId);
    return super.runAgent(forwarded, guardedSubscriber).then(result => {
      if (!terminal && !failed && !controller.signal.aborted) throw new Error("The agent stream ended before completion. Please try again.");
      return result;
    }).finally(() => { this.ownedResumes.delete(forwarded); });
  }
  protected onError(input: RunAgentInput, error: Error, subscribers: AgentSubscriber[]) {
    // The installed SDK logs its error argument even with debug disabled.
    // Keep status and rejection semantics while discarding the wire body.
    const safe = isCancellation(error)
      ? new DOMException("Request cancelled", "AbortError")
      : Object.assign(new Error("Agent request failed"), { status: (error as Error & { status?: number }).status });
    return super.onError(input, safe, subscribers);
  }
}

interface ThreadCallbacks {
  onSessionInvalid: () => void | Promise<void>;
  onAuthInvalid: () => void;
  onError: (message: string) => void;
  onRunSettled?: () => void | Promise<void>;
}

// One resource owns history requests, run cancellation and subscriptions. Each
// activation has a ticket so StrictMode restart cannot accept old completions.
export function createThreadSession(sessionId: string, callbacks: ThreadCallbacks, skill?: SkillVersionRef) {
  const agent = new PrivateHttpAgent({ url: apiURL("/api/agent/runs"), threadId: sessionId, debug: false, fetch: apiFetch }, skill);
  let active = true;
  let generation = 0;
  let subscriberGeneration = 0;
  let runGeneration = 0;
  let releaseSequence = 0;
  let cacheInvalidated = false;
  let controller = new AbortController();
  let unsubscribe = () => {};
  // Keep notification behind the existing per-run terminal/EOF guard, with
  // the generation from invocation rather than a later subscription.
  const runAgent = agent.runAgent.bind(agent);
  agent.runAgent = (parameters, subscriber) => {
    ++runGeneration;
    const ticket = subscriberGeneration;
    const startedActive = active;
    return runAgent(parameters, {
      ...subscriber,
      onRunFinalized: params => {
        if (startedActive && active && ticket === subscriberGeneration) void callbacks.onRunSettled?.();
        return subscriber?.onRunFinalized?.(params);
      },
    });
  };
  const current = (ticket: number) => active && ticket === generation;
  const failure = (ticket: number, status?: number, code?: string) => {
    if (!active || ticket !== subscriberGeneration) return;
    if (status === 401 || code === "not_signed_in") callbacks.onAuthInvalid();
    else if ((status === 404 || code === "not_found") && !agent.isApprovalResume && !agent.hasSelectedSkill) void callbacks.onSessionInvalid();
    else if (code !== "abort") callbacks.onError("The agent run failed. Please try again.");
  };
  const subscribe = () => {
    const ticket = subscriberGeneration;
    const subscription = agent.subscribe({
      onRunFailed: ({ error }) => { if (!isCancellation(error)) failure(ticket, (error as Error & { status?: number }).status); },
      onRunErrorEvent: ({ event }) => failure(ticket, undefined, event.code),
    });
    unsubscribe = subscription.unsubscribe;
  };
  subscribe();
  const history: ThreadHistoryAdapter = {
    load: async () => {
      const ticket = generation;
      try {
        if (!current(ticket)) throw new DOMException("Request cancelled", "AbortError");
        const result = await createSessionHistory(sessionId, controller.signal).load();
        if (!current(ticket)) throw new DOMException("Request cancelled", "AbortError");
        return result;
      } catch (error) {
        if (!current(ticket) || (error instanceof Error && error.name === "AbortError")) return ExportedMessageRepository.fromArray([]);
        if (error instanceof AuthenticationRequiredError) { callbacks.onAuthInvalid(); return ExportedMessageRepository.fromArray([]); }
        if (error instanceof SessionNotFoundError) { void callbacks.onSessionInvalid(); return ExportedMessageRepository.fromArray([]); }
        callbacks.onError("Could not load the conversation. Please try again.");
        return ExportedMessageRepository.fromArray([]);
      }
    },
    append: async () => undefined,
  };
  const dispose = () => {
    active = false;
    ++releaseSequence;
    ++generation;
    ++subscriberGeneration;
    unsubscribe();
    controller.abort();
    agent.abortRun();
    if (!cacheInvalidated) clearCitationCache();
    cacheInvalidated = true;
  };
  return {
    agent, history,
    resumeApproval: async (runId: string, approvalId: string, onHistoryLoaded?: (repository: ExportedMessageRepository) => void) => {
      if (!active) throw new Error("This conversation is no longer active.");
      const lifetime = generation;
      const ticket = subscriberGeneration;
      const runTicket = runGeneration + 1;
      const signal = controller.signal;
      const ownsReload = () => current(lifetime) && ticket === subscriberGeneration
        && runTicket === runGeneration && !signal.aborted;
      // RUN_ERROR also resolves the SDK promise; require a validated RUN_FINISHED.
      let finished = false;
      const result = await agent.resumeApproval(runId, approvalId, {
        onRunFinishedEvent: () => { finished = true; },
      });
      if (!finished || !onHistoryLoaded || !ownsReload()) return result;
      // Explicit approval runs bypass the runtime's rendering subscriber. Import
      // only successful authoritative history, never the adapter's error fallback.
      try {
        const repository = await createSessionHistory(sessionId, signal).load();
        if (repository && ownsReload()) onHistoryLoaded(repository);
      } catch (error) {
        if (!ownsReload()) return result;
        if (error instanceof AuthenticationRequiredError) callbacks.onAuthInvalid();
        else if (error instanceof SessionNotFoundError) void callbacks.onSessionInvalid();
        else if (!isCancellation(error)) callbacks.onError("The conversation could not be refreshed. Reload the page to see its recorded messages.");
      }
      return result;
    },
    activate: () => {
      ++releaseSequence;
      if (active) return;
      active = true;
      cacheInvalidated = false;
      if (controller.signal.aborted) { ++generation; controller = new AbortController(); }
      subscribe();
    },
    release: () => {
      active = false;
      ++subscriberGeneration;
      unsubscribe();
      agent.abortRun();
      clearCitationCache();
      cacheInvalidated = true;
      // The installed runtime retains its first history load promise during
      // StrictMode replay. Preserve that request if setup follows this cleanup
      // in the same tick; a real unmount aborts it on the next microtask.
      const ticket = ++releaseSequence;
      queueMicrotask(() => { if (!active && ticket === releaseSequence) dispose(); });
    },
    dispose,
  };
}
