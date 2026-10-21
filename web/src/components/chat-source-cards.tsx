import { useAuiState } from "@assistant-ui/react";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { chatSourceResults, type ChatSource, type ChatSourceResults } from "@/lib/chat-source-results";
import { importSource, SourceImportFailure } from "@/lib/source-import";
import { personalJupyterURL } from "@/lib/personal-preview";
import { usePersonalChatWorkspace } from "@/components/personal-chat-workspace";

type ImportState = { source: ChatSource; status: "pending" | "done" | "partial" | "unknown" | "error"; path?: string; message?: string; signIn?: boolean };
type ImportStore = { states: ReadonlyMap<string, ImportState>; subscribe: (listener: () => void) => () => void; snapshot: () => ReadonlyMap<string, ImportState>; set: (state: ImportState) => void };
const scopes = new WeakMap<(path: string) => void, ImportStore>();
const sourceKey = (source: ChatSource) => JSON.stringify([source.item, source.version]);
const buttonClass = "rounded-md border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50";

function importStore(open?: (path: string) => void): ImportStore {
  const existing = open && scopes.get(open);
  if (existing) return existing;
  const listeners = new Set<() => void>();
  const store: ImportStore = {
    states: new Map(),
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    snapshot: () => store.states,
    set(state) { store.states = new Map(store.states).set(sourceKey(state.source), state); for (const listener of listeners) listener(); },
  };
  if (open) scopes.set(open, store);
  return store;
}

export function ChatSourceCards() {
  const parts = useAuiState(state => state.message.parts);
  const results = useMemo(() => chatSourceResults(parts), [parts]);
  return <ChatSourceChoices results={results} />;
}

export function ChatSourceChoices({ results }: { results: ChatSourceResults }) {
  const workspace = usePersonalChatWorkspace();
  // The provider's stable open function belongs to one owner and workspace URL.
  // Share outcomes across messages without retaining another owner's identities.
  const store = useMemo(() => importStore(workspace?.open), [workspace?.open]);
  const states = useSyncExternalStore(store.subscribe, store.snapshot, store.snapshot);
  const [initiated, setInitiated] = useState<ReadonlySet<string>>(() => new Set());
  const request = useRef<{ controller: AbortController; source: ChatSource } | null>(null);
  useEffect(() => () => {
    const active = request.current; request.current = null;
    if (active) {
      active.controller.abort();
      store.set({ source: active.source, status: "unknown", message: "Import was cancelled. A personal file may have been created. Check JupyterLab before importing again." });
    }
  }, [store]);
  const configured = workspace?.configuration.status === "configured" ? workspace.configuration : undefined;
  const pending = [...states.values()].some(state => state.status === "pending");
  const importCopy = async (source: ChatSource) => {
    const previous = store.states.get(sourceKey(source));
    if (!configured || !workspace || request.current || [...store.states.values()].some(state => state.status === "pending") || previous && previous.status !== "error") return;
    const controller = new AbortController(); request.current = { controller, source };
    setInitiated(current => new Set(current).add(sourceKey(source)));
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
    if (outcome.status === "done" && outcome.path) workspace.open(outcome.path);
  };
  const status = (state: ImportState) => {
    if (state.status === "pending") return <p role="status" className="text-sm">Importing...</p>;
    if (state.status === "error") return <div role="alert" className="text-sm"><p>{state.message}</p>{configured && state.signIn && <a href={configured.url} target="_blank" rel="noopener noreferrer" className="underline">Sign in to JupyterLab</a>}</div>;
    if (state.status === "unknown") return <div role="alert" className="space-y-1 text-sm"><p>{state.message}{state.path && <> Check <span className="break-all">{state.path}</span>.</>}</p>{configured && <a href={personalJupyterURL(configured, state.path ?? "imports")} target="_blank" rel="noopener noreferrer" className="underline">Check in JupyterLab</a>}</div>;
    return <div role={state.status === "partial" ? "alert" : "status"} className="space-y-1 text-sm"><p>{state.status === "partial" ? "Personal copy saved at " : "Imported to "}<span className="break-all">{state.path}</span>. {state.message}</p>{state.path && workspace && configured && <div className="flex flex-wrap gap-3"><button type="button" className="underline" onClick={() => workspace.open(state.path!)}>Open copy</button><a href={personalJupyterURL(configured, state.path)} target="_blank" rel="noopener noreferrer" className="underline">Open in JupyterLab</a></div>}</div>;
  };
  const visible = new Set(results.sources.map(sourceKey));
  const retained = [...states.values()].filter(state => initiated.has(sourceKey(state.source)) && !visible.has(sourceKey(state.source)));
  if (!results.sources.length && !retained.length) return null;
  return <section aria-label="Source choices and personal imports" className="mt-3 space-y-2.5 rounded-xl border bg-muted/20 p-3">
    <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Source documents</h3>
    <p className="text-xs text-muted-foreground">Import copy saves the original version in your personal workspace. Maximum 16 MB; inline previews support up to 10 MB.</p>
    {!configured && <p role="status" className="text-sm">Connect your personal workspace to import a copy.</p>}
    {results.truncated && <p className="text-xs text-muted-foreground">Source choices are bounded. Refine your search for more results.</p>}
    <div className="grid gap-2 sm:grid-cols-2">
    {results.sources.map(source => {
      const state = states.get(sourceKey(source)), finished = state && state.status !== "pending" && state.status !== "error";
      const label = state?.status === "pending" ? "Importing..." : state?.status === "done" ? "Copy imported" : state?.status === "partial" ? "Copy saved" : state?.status === "unknown" ? "Check import" : state?.status === "error" ? "Retry Import" : "Import copy";
      return <article key={sourceKey(source)} className="flex flex-col gap-1.5 rounded-lg border bg-background p-3">
        <p className="break-words text-sm font-medium">{source.title.trim() || "Untitled document"}</p>
        <p className="truncate text-xs text-muted-foreground" title={source.item}>Source {source.item} · Version {source.version} · {source.classification}{source.simulated && " · Simulated"}</p>
        <p className="line-clamp-3 break-words text-sm text-foreground/80" title={source.snippet.trim()}>{source.snippet.trim() || "No sentence preview is available."}</p>
        <button type="button" className={buttonClass} aria-label={label + " for " + (source.title.trim() || "Untitled document") + ", source " + source.item + ", version " + source.version} disabled={!configured || pending || !!finished} onClick={() => void importCopy(source)}>{label}</button>
        {state && status(state)}
      </article>;
    })}
    </div>
    {retained.map(state => <article key={sourceKey(state.source)} className="space-y-2 rounded-md border p-3"><p className="text-sm font-medium">{state.source.title.trim() || "Untitled document"}</p><p className="break-words text-xs text-muted-foreground">Source {state.source.item} · Version {state.source.version} · {state.source.classification}{state.source.simulated && " · Simulated"}</p>{status(state)}</article>)}
  </section>;
}
