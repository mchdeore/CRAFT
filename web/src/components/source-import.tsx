import { useEffect, useRef, useState, type FormEvent } from "react";
import { importSource, searchSources, SourceImportFailure, type SourceResult } from "@/lib/source-import";
import { personalFileFormat, personalJupyterURL } from "@/lib/personal-preview";
import type { WorkspaceConfiguration } from "./workspace-url";

type SearchState = { status: "idle" | "pending" | "ready" | "error"; results: SourceResult[]; truncated?: boolean; message?: string };
type ImportState = { source: SourceResult; status: "pending" | "done" | "error" | "partial" | "unknown"; message?: string; path?: string; signIn?: boolean };
type SourceImportProps = { configuration: WorkspaceConfiguration & { status: "configured" }; onImported?: (path: string) => void; onOpenCopy?: (path: string) => void };
const buttonClass = "shrink-0 rounded-md border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50";
const sourceKey = (source: SourceResult) => source.item + ":" + source.version;
const sourceTitle = (source: SourceResult) => source.title.trim() || "Untitled document";

export function SourceImport(props: SourceImportProps) {
  return <SourceImportPanel key={props.configuration.url} {...props} />;
}

function SourceImportPanel({ configuration, onImported, onOpenCopy }: SourceImportProps) {
  const query = useRef<HTMLInputElement>(null);
  const searchRequest = useRef<AbortController | null>(null);
  const importRequest = useRef<AbortController | null>(null);
  const [search, setSearch] = useState<SearchState>({ status: "idle", results: [] });
  const [copies, setCopies] = useState<Map<string, ImportState>>(() => new Map());
  useEffect(() => () => { searchRequest.current?.abort(); importRequest.current?.abort(); }, []);

  const changed = () => {
    searchRequest.current?.abort(); searchRequest.current = null;
    setSearch({ status: "idle", results: [] });
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    searchRequest.current?.abort();
    const controller = new AbortController(); searchRequest.current = controller;
    setSearch({ status: "pending", results: [] });
    try {
      const result = await searchSources(query.current?.value ?? "", controller.signal);
      if (searchRequest.current === controller && !controller.signal.aborted) setSearch({ status: "ready", ...result });
    } catch (error) {
      if (searchRequest.current === controller && !controller.signal.aborted) setSearch({ status: "error", results: [], message: error instanceof SourceImportFailure ? error.message : "Source search is unavailable. Try again." });
    } finally {
      if (searchRequest.current === controller) searchRequest.current = null;
    }
  };
  const importResult = async (source: SourceResult) => {
    const key = sourceKey(source), previous = copies.get(key);
    if (importRequest.current || (previous && previous.status !== "error")) return;
    const controller = new AbortController(); importRequest.current = controller;
    setCopies(current => new Map(current).set(key, { source, status: "pending" }));
    let outcome: ImportState;
    try {
      const path = await importSource(source, configuration, controller.signal);
      outcome = { source, status: "done", path };
    } catch (error) {
      const failure = error instanceof SourceImportFailure ? error : undefined;
      outcome = {
        source, status: failure?.copiedPath ? "partial" : failure?.code === "outcome_unknown" ? "unknown" : "error",
        path: failure?.copiedPath ?? failure?.possiblePath,
        message: failure?.message ?? "Import could not finish. Open JupyterLab to check before trying again.",
        signIn: !failure || failure.code === "workspace_auth" || failure.code === "import_unavailable",
      };
    }
    if (importRequest.current !== controller) return;
    importRequest.current = null;
    if (controller.signal.aborted) return;
    setCopies(current => new Map(current).set(key, outcome));
    if ((outcome.status === "done" || outcome.status === "partial") && outcome.path) onImported?.(outcome.path);
    if (outcome.status === "done" && outcome.path) {
      const format = personalFileFormat(outcome.path);
      if (format === "docx" || format === "xlsx") onOpenCopy?.(outcome.path);
    }
  };

  const openCopy = (path: string) => {
    const format = personalFileFormat(path);
    return onOpenCopy && (format === "docx" || format === "xlsx")
      ? <button type="button" className="underline" onClick={() => onOpenCopy(path)}>Open copy</button>
      : <a href={personalJupyterURL(configuration, path)} target="_blank" rel="noopener noreferrer" className="underline">Open copy</a>;
  };
  const copyStatus = (state: ImportState) => {
    if (state.status === "pending") return <p role="status" className="text-sm text-muted-foreground">Importing... You can keep searching while your personal copy saves.</p>;
    if (state.status === "error") return <div role="alert" className="space-y-1 text-sm"><p>{state.message}</p>{state.signIn && <a href={configuration.url} target="_blank" rel="noopener noreferrer" className="underline">Sign in to JupyterLab</a>}</div>;
    if (state.status === "unknown") return <div role="alert" className="space-y-1 text-sm">
      <p>{state.message}{state.path && <> Check <span className="break-all font-medium">{state.path}</span>.</>}</p>
      <a href={personalJupyterURL(configuration, state.path ?? "imports")} target="_blank" rel="noopener noreferrer" className="underline">Check in JupyterLab</a>
    </div>;
    if (state.status === "partial" && state.path) return <p role="alert" className="break-words text-sm">Personal copy saved at <span className="font-medium">{state.path}</span>. {state.message} {openCopy(state.path)}</p>;
    if (state.status === "done" && state.path) return <p role="status" className="break-words text-sm">Imported to <span className="font-medium">{state.path}</span>. {openCopy(state.path)}</p>;
    return null;
  };
  const pending = [...copies.values()].some(state => state.status === "pending");
  const visible = new Set(search.results.map(sourceKey));
  const otherCopies = [...copies.entries()].filter(([key]) => !visible.has(key));

  return <section aria-label="Source search and import" className="shrink-0 space-y-3 border-b px-4 py-3">
    <form aria-label="Search sources" className="flex flex-wrap items-end gap-2" onSubmit={event => void submit(event)}>
      <div className="min-w-0 flex-1 space-y-1">
        <label htmlFor="source-query" className="text-sm font-medium">Search sources</label>
        <input ref={query} id="source-query" name="source-query" type="search" maxLength={500} required onInput={changed} placeholder="Search original documents" className="h-10 w-full rounded-md border bg-background px-3 text-sm" />
      </div>
      <button type="submit" disabled={search.status === "pending"} className={buttonClass}>{search.status === "pending" ? "Searching..." : "Search"}</button>
    </form>
    <p className="text-xs text-muted-foreground">Import copy saves an editable personal file in your workspace. Originals stay read-only. Maximum 16 MB.</p>
    {search.status === "idle" && <p className="text-sm text-muted-foreground">Search for a document title or a phrase from its text.</p>}
    {search.status === "pending" && <p role="status" className="text-sm text-muted-foreground">Searching original documents...</p>}
    {search.status === "error" && <p role="alert" className="text-sm">{search.message} Submit your search to retry.</p>}
    {search.status === "ready" && search.results.length === 0 && <p role="status" className="text-sm text-muted-foreground">No matching sources. Try another title or phrase.</p>}
    {search.status === "ready" && search.results.length > 0 && <p role="status" className="text-xs text-muted-foreground">{search.results.length} matching {search.results.length === 1 ? "source" : "sources"} shown.</p>}
    {search.truncated && <p className="text-xs text-muted-foreground">Showing the first results. Refine your search for more.</p>}
    {search.results.length > 0 && <div className="space-y-3">
      {search.results.map(source => {
        const state = copies.get(sourceKey(source));
        const finished = state && state.status !== "error" && state.status !== "pending";
        const label = state?.status === "pending" ? "Importing..." : state?.status === "done" ? "Copy imported" : state?.status === "partial" ? "Copy saved" : state?.status === "unknown" ? "Check import" : state?.status === "error" ? "Retry Import" : "Import copy";
        return <article key={sourceKey(source)} className="space-y-3 rounded-md border p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0 space-y-1">
              <h3 className="break-words text-sm font-medium">{sourceTitle(source)}</h3>
              <p className="break-words text-xs text-muted-foreground">Source {source.item} · Version {source.version} · {source.classification}</p>
            </div>
            <button type="button" disabled={pending || !!finished} aria-label={label + " for " + sourceTitle(source) + ", version " + source.version} onClick={() => void importResult(source)} className={buttonClass}>{label}</button>
          </div>
          <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-muted-foreground">{source.snippet.trim() || "No sentence preview is available."}</p>
          {state && copyStatus(state)}
        </article>;
      })}
    </div>}
    {otherCopies.length > 0 && <div aria-label="Import status" className="space-y-2">
      <h3 className="text-sm font-medium">Your imports</h3>
      {otherCopies.map(([key, state]) => <article key={key} className="space-y-2 rounded-md border p-3">
        <p className="break-words text-sm font-medium">{sourceTitle(state.source)}</p>
        <p className="break-words text-xs text-muted-foreground">Source {state.source.item} · Version {state.source.version}</p>
        {copyStatus(state)}
      </article>)}
    </div>}
  </section>;
}
