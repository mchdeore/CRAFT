import { apiFetch } from "@/lib/api-client";
import type { ConversationSummary, Me } from "craft-api/api/types";
import { useEffect, useState, useSyncExternalStore } from "react";
import { AuthenticationRequiredError } from "@/lib/agent-history";
import { copySkillSelection, type SkillSelection } from "@/lib/skills";

const pointerKey = (owner: string) => `craft-conversation:${owner}`;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function clearConversationPointer(owner: string) {
  try { sessionStorage.removeItem(pointerKey(owner)); } catch { /* Storage is optional. */ }
}
interface SessionState {
  sessionID: string; selectedSkill?: SkillSelection; error?: string; notice?: string; pending: boolean;
  history: ConversationSummary[]; historyPending: boolean; historyError?: string; historyLimited: boolean;
}
class ConversationError extends Error {}
const emptyState = (): SessionState => ({ sessionID: "", selectedSkill: undefined, pending: false, history: [], historyPending: false, historyLimited: false });
const summaryOnly = ({ id, title, updatedAt, archived, pendingApprovals }: ConversationSummary): ConversationSummary => ({ id, title, updatedAt, archived, pendingApprovals });
const newest = (entries: ConversationSummary[]) => [...entries].sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt) || a.id.localeCompare(b.id));

// Only an ID is kept in tab storage. Every restored ID is checked through an
// owned GET after Me; neither stored IDs nor another owner's state are trusted.
export class ConversationSession {
  private readonly owner: string;
  private state: SessionState = { ...emptyState(), pending: true };
  private listeners = new Set<() => void>();
  private controller?: AbortController;
  private listController?: AbortController;
  private sequence = 0;
  private listSequence = 0;
  private active = true;
  private action?: "start" | "create" | "select" | "replace";
  private verified?: ConversationSummary;
  onAuthInvalid: () => void = () => {};
  constructor(owner: string, onAuthInvalid: () => void = () => {}) { this.owner = owner; this.onAuthInvalid = onAuthInvalid; }
  getSnapshot = (): SessionState => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(state: Partial<SessionState>) { this.state = { ...this.state, error: undefined, notice: undefined, ...state }; for (const listener of this.listeners) listener(); }
  private cancelList() { ++this.listSequence; this.listController?.abort(); }
  private begin(action: NonNullable<ConversationSession["action"]>) {
    this.controller?.abort(); this.cancelList(); this.controller = new AbortController(); this.action = action;
    return { sequence: ++this.sequence, signal: this.controller.signal };
  }
  private current(sequence: number) { return this.active && sequence === this.sequence; }
  private check(sequence: number) { if (!this.current(sequence)) throw new DOMException("Request cancelled", "AbortError"); }
  private async request(url: string, options: RequestInit, sequence: number) {
    const response = await apiFetch(url, { ...options, credentials: "same-origin" });
    this.check(sequence);
    if (response.status === 401) throw new AuthenticationRequiredError();
    return response;
  }
  private accept(conversation: ConversationSummary, notice?: string, writePointer = true, selectedSkill?: SkillSelection) {
    this.verified = summaryOnly(conversation);
    if (writePointer) { try { sessionStorage.setItem(pointerKey(this.owner), conversation.id); } catch { /* Storage is optional. */ } }
    const history = newest([this.verified, ...this.state.history.filter(entry => entry.id !== conversation.id)]).slice(0, 100);
    this.action = undefined; this.update({ sessionID: conversation.id, selectedSkill, pending: false, historyPending: false, history, notice });
  }
  private async create(sequence: number, signal: AbortSignal): Promise<ConversationSummary> {
    const response = await this.request("/api/conversations", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", signal }, sequence);
    if (!response.ok) throw new ConversationError("Session initialization failed. Try again.");
    const conversation = await response.json(); this.check(sequence);
    if (!isConversationSummary(conversation) || conversation.archived) throw new ConversationError("The server returned an invalid conversation.");
    return summaryOnly(conversation);
  }
  private invalidate() {
    this.active = false; ++this.sequence; this.controller?.abort(); this.cancelList(); this.verified = undefined; this.action = undefined;
    clearConversationPointer(this.owner); this.update(emptyState()); this.onAuthInvalid();
  }
  private failed(error: unknown, sequence: number, previous = "") {
    if (!this.current(sequence)) return;
    if (error instanceof AuthenticationRequiredError) { this.invalidate(); return; }
    this.action = undefined;
    this.update({ sessionID: previous, selectedSkill: previous ? this.state.selectedSkill : undefined, pending: false, historyPending: false, error: error instanceof ConversationError ? error.message : "Could not open the conversation. Try again." });
  }
  start = async (): Promise<void> => {
    this.active = true; const { sequence, signal } = this.begin("start");
    this.verified = undefined; this.update({ sessionID: "", selectedSkill: undefined, pending: true, historyPending: false });
    try {
      let stored: string | null = null;
      try { sessionStorage.removeItem("pi-agent-session-id"); stored = sessionStorage.getItem(pointerKey(this.owner)); } catch { /* Storage is optional. */ }
      if (stored) {
        if (!UUID.test(stored)) { clearConversationPointer(this.owner); stored = null; }
      }
      if (stored) {
        const response = await this.request(`/api/conversations/${encodeURIComponent(stored)}`, { signal }, sequence);
        if (response.status !== 404) {
          if (!response.ok) throw new ConversationError("Could not verify the conversation. Try again.");
          const conversation = await response.json(); this.check(sequence);
          if (!isConversationSummary(conversation) || conversation.id !== stored) throw new ConversationError("The server returned an invalid conversation.");
          if (!conversation.archived) { this.accept(conversation, undefined, false); return; }
        }
        clearConversationPointer(this.owner);
      }
      const conversation = await this.create(sequence, signal); this.check(sequence); this.accept(conversation);
    } catch (error) { this.failed(error, sequence); }
  };
  private async replace(archive: boolean, notice?: string) {
    if (this.state.pending || !this.active) return;
    const previous = this.state.sessionID; let archived = false; const { sequence, signal } = this.begin("replace");
    this.update({ sessionID: "", pending: true, historyPending: false });
    try {
      if (archive && previous) {
        const response = await this.request(`/api/conversations/${encodeURIComponent(previous)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ archived: true }), signal }, sequence);
        if (!response.ok) throw new ConversationError("Could not archive the conversation. Reset was not completed.");
        archived = true;
        this.update({ ...this.state, history: this.state.history.filter(entry => entry.id !== previous) });
      }
      this.check(sequence); clearConversationPointer(this.owner); this.verified = undefined;
      const conversation = await this.create(sequence, signal); this.check(sequence); this.accept(conversation, notice);
    } catch (error) { this.failed(error, sequence, archive && !archived ? previous : ""); }
  }
  newChat = async (skill?: SkillSelection): Promise<boolean> => {
    const selectedSkill = skill === undefined ? undefined : copySkillSelection(skill);
    if (!this.active || (this.state.pending && this.action !== "select")) return false;
    const previous = this.verified?.id ?? ""; const { sequence, signal } = this.begin("create");
    this.update({ sessionID: "", pending: true, historyPending: false });
    try {
      const conversation = await this.create(sequence, signal); this.check(sequence);
      if (conversation.id === previous) throw new ConversationError("The server did not create a new conversation. Try again.");
      this.accept(conversation, undefined, true, selectedSkill); return true;
    }
    catch (error) { this.failed(error, sequence, previous); return false; }
  };
  selectChat = async (id: string): Promise<void> => {
    if (!this.active || (this.state.pending && this.action !== "select" && this.action !== "create")) return;
    if (!UUID.test(id)) { this.update({ ...this.state, error: "Choose a valid conversation." }); return; }
    const previous = this.verified?.id ?? ""; const { sequence, signal } = this.begin("select");
    this.update({ sessionID: "", pending: true, historyPending: false });
    try {
      const response = await this.request(`/api/conversations/${encodeURIComponent(id)}`, { signal }, sequence);
      if (!response.ok) throw new ConversationError("Could not open that conversation. Try again.");
      const conversation = await response.json(); this.check(sequence);
      if (!isConversationSummary(conversation) || conversation.id !== id || conversation.archived) throw new ConversationError("That conversation is unavailable.");
      this.accept(conversation);
    } catch (error) { this.failed(error, sequence, previous); }
  };
  refreshHistory = async (): Promise<void> => {
    if (!this.active) return;
    this.cancelList(); this.listController = new AbortController();
    const sequence = this.listSequence, selection = this.sequence;
    const current = () => this.active && sequence === this.listSequence && selection === this.sequence;
    this.update({ ...this.state, historyPending: true, historyError: undefined });
    try {
      const response = await apiFetch("/api/conversations", { signal: this.listController.signal, credentials: "same-origin" });
      if (!current()) return;
      if (response.status === 401) { this.invalidate(); return; }
      if (!response.ok) throw new ConversationError("Could not load chat history. Try again.");
      const body: unknown = await response.json(); if (!current()) return;
      if (!Array.isArray(body) || !body.every(isConversationSummary) || new Set(body.map(entry => entry.id)).size !== body.length) throw new ConversationError("The server returned invalid chat history. Try again.");
      const available = newest(body.filter(entry => !entry.archived).map(summaryOnly));
      this.update({ ...this.state, history: available.slice(0, 100), historyPending: false, historyError: undefined, historyLimited: body.length >= 100 });
    } catch (error) {
      if (!current()) return;
      this.update({ ...this.state, history: [], historyPending: false, historyLimited: false, historyError: error instanceof ConversationError ? error.message : "Could not load chat history. Try again." });
    }
  };
  mount = () => {
    let cancelled = false;
    queueMicrotask(() => { if (!cancelled) void this.start(); });
    return () => { cancelled = true; this.dispose(); };
  };
  reset = () => this.replace(true);
  renew = () => this.replace(false, "Your previous conversation is unavailable. A new conversation was started; please resend your message.");
  dismissNotice = () => { this.update({ ...this.state, notice: undefined }); };
  dispose = () => { this.active = false; ++this.sequence; this.controller?.abort(); this.cancelList(); this.verified = undefined; this.action = undefined; this.update(emptyState()); };
}
function isConversationSummary(value: unknown): value is ConversationSummary {
  return typeof value === "object" && value !== null && "id" in value && typeof value.id === "string" && UUID.test(value.id)
    && "title" in value && typeof value.title === "string" && value.title.length <= 200
    && "updatedAt" in value && typeof value.updatedAt === "string" && Number.isFinite(Date.parse(value.updatedAt))
    && "archived" in value && typeof value.archived === "boolean"
    && "pendingApprovals" in value && typeof value.pendingApprovals === "number" && Number.isSafeInteger(value.pendingApprovals) && value.pendingApprovals >= 0;
}
export function useAgentSession(owner: Me, onAuthInvalid: () => void) {
  const [session] = useState(() => new ConversationSession(owner.id, onAuthInvalid));
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
  useEffect(() => session.mount(), [session]);
  useEffect(() => { if (state.sessionID) void session.refreshHistory(); }, [session, state.sessionID]);
  return { ...state, renewSession: session.renew, resetSession: session.reset, dismissNotice: session.dismissNotice, retrySession: session.start, newChat: session.newChat, selectChat: session.selectChat, refreshHistory: session.refreshHistory };
}
