import { useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import type { WorkspaceConfiguration } from "@/components/workspace-url";
import { importSource, SourceImportFailure, type SourceResult } from "@/lib/source-import";

export type ImportState = { source: SourceResult & { simulated?: boolean }; status: "pending" | "done" | "partial" | "unknown" | "error"; path?: string; message?: string; signIn?: boolean };
type ImportStore = { states: ReadonlyMap<string, ImportState>; subscribe: (listener: () => void) => () => void; snapshot: () => ReadonlyMap<string, ImportState>; set: (state: ImportState) => void; reset: (source: SourceResult) => void };
const scopes = new WeakMap<WorkspaceConfiguration, ImportStore>();
export const sourceKey = (source: SourceResult) => JSON.stringify([source.item, source.version]);

function importStore(configuration?: WorkspaceConfiguration): ImportStore {
  const existing = configuration && scopes.get(configuration);
  if (existing) return existing;
  const listeners = new Set<() => void>();
  const store: ImportStore = {
    states: new Map(),
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    snapshot: () => store.states,
    reset(source) { const next = new Map(store.states); next.delete(sourceKey(source)); store.states = next; for (const listener of listeners) listener(); },
    set(state) { store.states = new Map(store.states).set(sourceKey(state.source), state); for (const listener of listeners) listener(); },
  };
  if (configuration) scopes.set(configuration, store);
  return store;
}

export function useSourceImport(configuration?: WorkspaceConfiguration) {
  const store = useMemo(() => importStore(configuration), [configuration]);
  const state = useSyncExternalStore(store.subscribe, store.snapshot, store.snapshot);
  const request = useRef<{ controller: AbortController; source: SourceResult } | null>(null);
  useEffect(() => () => {
    const active = request.current; request.current = null;
    if (active) {
      active.controller.abort();
      store.set({ source: active.source, status: "unknown", message: "Import was cancelled. A personal file may have been created. Check JupyterLab before importing again." });
    }
  }, [store]);
  const configured = configuration?.status === "configured" ? configuration : undefined;
  const start = async (source: SourceResult) => {
    const previous = store.states.get(sourceKey(source));
    if (!configured || request.current || [...store.states.values()].some(state => state.status === "pending") || previous && previous.status !== "error") return;
    const controller = new AbortController(); request.current = { controller, source };
    store.set({ source, status: "pending" });
    let outcome: ImportState;
    try {
      const path = await importSource(source, configured, controller.signal);
      outcome = { source, status: "done", path };
    } catch (error) {
      const failure = error instanceof SourceImportFailure ? error : undefined;
      outcome = {
        source, status: failure?.copiedPath ? "partial" : failure?.code === "outcome_unknown" || !failure ? "unknown" : "error",
        path: failure?.copiedPath ?? failure?.possiblePath,
        message: failure?.message ?? "The import result could not be confirmed. Check JupyterLab before importing again.",
        signIn: failure?.code === "workspace_auth" || failure?.code === "import_unavailable",
      };
    }
    if (request.current?.controller !== controller || controller.signal.aborted) return;
    request.current = null; store.set(outcome);
    return outcome;
  };
  const reset = (source: SourceResult) => {
    if (request.current || store.states.get(sourceKey(source))?.status === "pending") return;
    store.reset(source);
  };
  return { state, start, reset };
}
