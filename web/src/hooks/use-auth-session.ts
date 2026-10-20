import { apiFetch } from "@/lib/api-client";
import type { Me, SignIn } from "craft-api/api/types";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { clearCitationCache } from "@/components/citations/citation-records";
import { clearConversationPointer } from "@/hooks/use-agent-session";

type AuthStatus = "checking" | "signed-out" | "signing-in" | "signed-in" | "signing-out" | "logout-failed" | "error";
interface AuthState { status: AuthStatus; me: Me | null; generation: number; error?: string }

class AuthFailure extends Error {}

// Auth mutations share a queue: a late login must finish before its logout can
// revoke the resulting cookie. Identity changes take effect locally at once.
export class AuthSession {
  private state: AuthState = { status: "checking", me: null, generation: 0 };
  private listeners = new Set<() => void>();
  private sequence = 0;
  private active = true;
  private checkController?: AbortController;
  private mutations: Promise<void> = Promise.resolve();
  getSnapshot = (): AuthState => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(status: AuthStatus, me: Me | null = null, error?: string) {
    const previousOwner = this.state.me?.id;
    if (previousOwner && previousOwner !== me?.id) clearConversationPointer(previousOwner);
    if (previousOwner !== me?.id) clearCitationCache();
    this.state = { status, me, error, generation: this.state.generation + 1 };
    for (const listener of this.listeners) listener();
  }
  start = async (): Promise<void> => {
    this.active = true;
    try { sessionStorage.removeItem("craft-mock-sessions"); sessionStorage.removeItem("pi-agent-session-id"); } catch { /* Storage is optional. */ }
    const sequence = ++this.sequence;
    this.checkController?.abort();
    const controller = new AbortController();
    this.checkController = controller;
    this.update("checking");
    try {
      const response = await apiFetch("/api/me", { credentials: "same-origin", signal: controller.signal });
      if (!this.current(sequence)) return;
      if (response.status === 401) { this.update("signed-out"); return; }
      if (!response.ok) throw new AuthFailure(authFailure(response.status));
      const me = readMe(await response.json());
      if (this.current(sequence)) this.update("signed-in", me);
    } catch (error) {
      if (this.current(sequence)) this.update("error", null, safeMessage(error));
    }
  };
  private current(sequence: number) { return this.active && this.sequence === sequence; }
  private queue(operation: () => Promise<void>): Promise<void> {
    const next = this.mutations.then(operation);
    this.mutations = next.catch(() => undefined);
    return next;
  }
  signIn = (input: SignIn): Promise<void> => {
    if (this.state.status === "logout-failed" || this.state.status === "signing-out") return Promise.resolve();
    const sequence = ++this.sequence;
    this.checkController?.abort();
    this.update("signing-in");
    return this.queue(async () => {
      if (!this.current(sequence)) return;
      try {
        const response = await apiFetch("/api/session", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) });
        if (!response.ok) throw new AuthFailure(authFailure(response.status));
        const me = readMe(await response.json());
        if (this.current(sequence)) this.update("signed-in", me);
      } catch (error) {
        if (this.current(sequence)) this.update("signed-out", null, safeMessage(error));
      }
    });
  };
  signOut = (): Promise<void> => {
    const sequence = ++this.sequence;
    this.checkController?.abort();
    this.update("signing-out");
    return this.queue(async () => {
      try {
        const response = await apiFetch("/api/session", { method: "DELETE", credentials: "same-origin" });
        if (response.status !== 204) throw new Error("Logout not confirmed");
        if (this.current(sequence)) this.update("signed-out");
      } catch {
        if (this.current(sequence)) this.update("logout-failed", null, "Sign-out was not confirmed. Retry sign-out to revoke the session.");
      }
    });
  };
  invalidate = (generation = this.state.generation) => {
    if (!this.active || this.state.status !== "signed-in" || generation !== this.state.generation) return;
    ++this.sequence;
    this.checkController?.abort();
    this.update("signed-out", null, "Your sign-in expired. Sign in again.");
  };
  dispose = () => { this.active = false; ++this.sequence; this.checkController?.abort(); };
}

function readMe(value: unknown): Me {
  if (typeof value !== "object" || value === null || !("id" in value) || typeof value.id !== "string" || !value.id || !("username" in value) || typeof value.username !== "string" || !("displayName" in value) || typeof value.displayName !== "string" || !("roles" in value) || !Array.isArray(value.roles) || !value.roles.every(role => role === "member" || role === "admin")) throw new AuthFailure("The server returned an invalid account.");
  return { id: value.id, username: value.username, displayName: value.displayName, roles: [...value.roles] };
}
function authFailure(status: number) {
  if (status === 401) return "Sign-in was refused. Check your username and password.";
  if (status === 403) return "Sign-in was refused by the server's access policy.";
  if (status === 429) return "Too many sign-in attempts. Try again later.";
  return "The sign-in service is unavailable. Try again.";
}
function safeMessage(error: unknown) { return error instanceof AuthFailure ? error.message : "The sign-in service is unavailable. Try again."; }

export function useAuthSession() {
  const [session] = useState(() => new AuthSession());
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
  useEffect(() => { void session.start(); return session.dispose; }, [session]);
  const invalidate = useCallback(() => session.invalidate(state.generation), [session, state.generation]);
  return { ...state, signIn: session.signIn, signOut: session.signOut, invalidate, retry: session.start };
}
