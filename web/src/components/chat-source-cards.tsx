import { useAuiState } from "@assistant-ui/react";
import { ChevronDownIcon, FilesIcon, FileTextIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { chatSourceResults, type ChatSource, type ChatSourceResults } from "@/lib/chat-source-results";
import { useSourceImport, sourceKey, type ImportState } from "@/hooks/use-source-import";
import { personalJupyterURL } from "@/lib/personal-preview";
import { useCitations } from "@/components/citations/citation-context";
import { usePersonalChatWorkspace } from "@/components/personal-chat-workspace";
import { useMissionTitles } from "@/hooks/use-mission-titles";
import { documentLabel, documentScope } from "@/lib/document-labels";

const buttonClass = "rounded-md border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50";

// One turn can span several assistant messages, one per step. Its sources show
// once, collapsed under the turn's last message, so the answer stays on top.
export function ChatSourceCards() {
  const messages = useAuiState(state => state.thread.messages);
  const index = useAuiState(state => state.message.index);
  const results = useMemo(() => {
    const next = messages[index + 1];
    if (next && next.role === "assistant") return null;
    let first = index;
    while (first > 0 && messages[first - 1]?.role === "assistant") first--;
    return chatSourceResults(messages.slice(first, index + 1).flatMap(message => message.parts));
  }, [messages, index]);
  return results ? <ChatSourceChoices results={results} /> : null;
}

export function ChatSourceChoices({ results }: { results: ChatSourceResults }) {
  const workspace = usePersonalChatWorkspace();
  const { openSource } = useCitations();
  const { state: states, start } = useSourceImport(workspace?.configuration);
  const [initiated, setInitiated] = useState<ReadonlySet<string>>(() => new Set());
  const visible = new Set(results.sources.map(sourceKey));
  const retained = [...states.values()].filter(state => initiated.has(sourceKey(state.source)) && !visible.has(sourceKey(state.source))).map(state => ({ ...state, tags: (state.source as Partial<ChatSource>).tags }));
  const missions = useMissionTitles([...results.sources.map(source => source.tags?.mission), ...retained.map(state => state.tags?.mission)]);
  const configured = workspace?.configuration.status === "configured" ? workspace.configuration : undefined;
  const pending = [...states.values()].some(state => state.status === "pending");
  const importCopy = async (source: ChatSource) => {
    setInitiated(current => new Set(current).add(sourceKey(source)));
    const outcome = await start(source);
    if (outcome?.status === "done" && outcome.path) workspace?.open(outcome.path);
  };
  const status = (state: ImportState) => {
    if (state.status === "pending") return <p role="status" className="text-sm">Importing...</p>;
    if (state.status === "error") return <div role="alert" className="text-sm"><p>{state.message}</p>{configured && state.signIn && <a href={configured.url} target="_blank" rel="noopener noreferrer" className="underline">Sign in to JupyterLab</a>}</div>;
    if (state.status === "unknown") return <div role="alert" className="space-y-1 text-sm"><p>{state.message}{state.path && <> Check <span className="break-all">{state.path}</span>.</>}</p>{configured && <a href={personalJupyterURL(configured, state.path ?? "imports")} target="_blank" rel="noopener noreferrer" className="underline">Check in JupyterLab</a>}</div>;
    return <div role={state.status === "partial" ? "alert" : "status"} className="space-y-1 text-sm"><p>{state.status === "partial" ? "Personal copy saved at " : "Imported to "}<span className="break-all">{state.path}</span>. {state.message}</p>{state.path && workspace && configured && <div className="flex flex-wrap gap-3"><button type="button" className="underline" onClick={() => workspace.open(state.path!)}>Open copy</button><a href={personalJupyterURL(configured, state.path)} target="_blank" rel="noopener noreferrer" className="underline">Open in JupyterLab</a></div>}</div>;
  };
  if (!results.sources.length && !retained.length) return null;
  const titles = results.sources.map(source => documentLabel(source.title, source.tags, missions));
  return <details className="group mt-3 rounded-xl border bg-muted/20 open:bg-muted/30">
    <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
      <FilesIcon className="size-3.5 shrink-0" />
      <span className="shrink-0 font-medium text-foreground/80">{titles.length + retained.length} source {titles.length + retained.length === 1 ? "document" : "documents"}</span>
      <span className="min-w-0 flex-1 truncate">{titles.slice(0, 4).join(" · ")}</span>
      <ChevronDownIcon className="size-3.5 shrink-0 -rotate-90 transition-transform group-open:rotate-0" />
    </summary>
  <section aria-label="Source choices and personal imports" className="space-y-2.5 border-t p-3">
    <p className="text-xs text-muted-foreground">Choose a document to preview it. Import a personal copy to edit and analyse it; the source stays unchanged.</p>
    {!configured && <p role="status" className="text-sm">Connect your personal workspace to import a copy.</p>}
    {results.truncated && <p className="text-xs text-muted-foreground">Source choices are bounded. Refine your search for more results.</p>}
    <div className="grid gap-2 sm:grid-cols-2">
    {results.sources.map((source, index) => {
      const state = states.get(sourceKey(source)), finished = state && state.status !== "pending" && state.status !== "error";
      const label = state?.status === "pending" ? "Importing..." : state?.status === "done" ? "Copy imported" : state?.status === "partial" ? "Copy saved" : state?.status === "unknown" ? "Check import" : state?.status === "error" ? "Retry Import" : "Import copy";
      return <article key={sourceKey(source)} className="flex flex-col gap-1.5 rounded-lg border bg-background p-3">
        <button type="button" className="flex items-start gap-2 break-words text-left text-sm font-medium text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring" onClick={() => {
          const base = `/api/library/documents/${encodeURIComponent(source.item)}`;
          openSource({ title: source.title, classification: source.classification, sourceID: source.item, version: String(source.version), mediaType: "", previewURL: `${base}/preview?version=${source.version}`, downloadURL: `${base}/download?version=${source.version}` });
        }}><FileTextIcon className="mt-0.5 size-4 shrink-0" /><span>{index + 1}. {documentLabel(source.title, source.tags, missions)}<span className="mt-1 block text-xs font-normal">Preview document →</span></span></button>
        <p className="text-xs text-muted-foreground">{documentScope(source.version, source.classification, source.simulated, source.tags)}</p>
        <p className="line-clamp-3 break-words text-sm text-foreground/80" title={source.snippet.trim()}>{source.snippet.trim() || "No sentence preview is available."}</p>
        <button type="button" className={buttonClass} aria-label={label + " for " + titles[index] + ", version " + source.version} disabled={!configured || pending || !!finished} onClick={() => void importCopy(source)}>{label}</button>
        {state && status(state)}
      </article>;
    })}
    </div>
    {retained.map(state => <article key={sourceKey(state.source)} className="space-y-2 rounded-md border p-3"><p className="text-sm font-medium">{documentLabel(state.source.title, state.tags, missions)}</p><p className="break-words text-xs text-muted-foreground">{documentScope(state.source.version, state.source.classification, state.source.simulated, state.tags)}</p>{status(state)}</article>)}
  </section>
  </details>;
}
