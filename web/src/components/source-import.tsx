import { useEffect, useRef, useState, type FormEvent } from "react";
import { importSource, searchSources, SourceImportFailure, type SourceResult } from "@/lib/source-import";
import { personalFileFormat, personalJupyterURL } from "@/lib/personal-preview";
import type { WorkspaceConfiguration } from "./workspace-url";

type SearchState = { status: "idle" | "pending" | "ready" | "error"; results: SourceResult[]; truncated?: boolean; message?: string };
type ImportState = { item: string; status: "pending" | "done" | "error" | "partial"; message?: string; path?: string; signIn?: boolean };
const buttonClass = "shrink-0 rounded-md border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50";

export function SourceImport({ configuration, onImported, onOpenCopy }: { configuration: WorkspaceConfiguration & { status: "configured" }; onImported?: (path: string) => void; onOpenCopy?: (path: string) => void }) {
  const query = useRef<HTMLInputElement>(null);
  const searchRequest = useRef<AbortController | null>(null);
  const importRequest = useRef<AbortController | null>(null);
  const [search, setSearch] = useState<SearchState>({ status: "idle", results: [] });
  const [copy, setCopy] = useState<ImportState | null>(null);
  useEffect(() => () => { searchRequest.current?.abort(); importRequest.current?.abort(); }, [configuration.url]);

  const changed = () => {
    searchRequest.current?.abort(); importRequest.current?.abort();
    setSearch({ status: "idle", results: [] }); setCopy(null);
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    searchRequest.current?.abort(); importRequest.current?.abort();
    const controller = new AbortController(); searchRequest.current = controller;
    setSearch({ status: "pending", results: [] }); setCopy(null);
    try {
      const result = await searchSources(query.current?.value ?? "", controller.signal);
      if (!controller.signal.aborted) setSearch({ status: "ready", ...result });
    } catch (error) {
      if (!controller.signal.aborted) setSearch({ status: "error", results: [], message: error instanceof SourceImportFailure ? error.message : "Source search is unavailable. Try again." });
    }
  };
  const importResult = async (source: SourceResult) => {
    importRequest.current?.abort();
    const controller = new AbortController(); importRequest.current = controller;
    setCopy({ item: source.item, status: "pending" });
    try {
      const path = await importSource(source, configuration, controller.signal);
      if (!controller.signal.aborted) { setCopy({ item: source.item, status: "done", path }); onImported?.(path); }
    } catch (error) {
      if (!controller.signal.aborted) setCopy({ item: source.item, status: error instanceof SourceImportFailure && error.copiedPath ? "partial" : "error", path: error instanceof SourceImportFailure ? error.copiedPath : undefined, message: error instanceof SourceImportFailure ? error.message : "Import could not finish. Try again.", signIn: !(error instanceof SourceImportFailure) || error.code === "workspace_auth" || error.code === "import_unavailable" });
    }
  };

  const openCopy = (path: string) => {
    const format = personalFileFormat(path);
    return onOpenCopy && (format === "docx" || format === "xlsx")
      ? <button type="button" className="underline" onClick={() => onOpenCopy(path)}>Open copy</button>
      : <a href={personalJupyterURL(configuration, path)} target="_blank" rel="noopener noreferrer" className="underline">Open copy</a>;
  };

  return <section aria-label="Source search and import" className="shrink-0 space-y-3 border-b px-4 py-3">
    <form aria-label="Search sources" className="flex items-end gap-2" onSubmit={event => void submit(event)}>
      <div className="min-w-0 flex-1 space-y-1">
        <label htmlFor="source-query" className="text-sm font-medium">Search sources</label>
        <input ref={query} id="source-query" name="source-query" type="search" maxLength={500} required onInput={changed} placeholder="Search original documents" className="h-10 w-full rounded-md border bg-background px-3 text-sm" />
      </div>
      <button type="submit" disabled={search.status === "pending"} className={buttonClass}>{search.status === "pending" ? "Searching..." : "Search"}</button>
    </form>
    <p className="text-xs text-muted-foreground">Import creates a personal copy in your workspace. Originals stay read-only. Maximum 16 MB.</p>
    {search.status === "error" && <p role="alert" className="text-sm">{search.message} Submit your search to retry.</p>}
    {search.status === "ready" && search.results.length === 0 && <p role="status" className="text-sm text-muted-foreground">No matching sources.</p>}
    {search.truncated && <p className="text-xs text-muted-foreground">Showing the first results. Refine your search for more.</p>}
    {search.results.length > 0 && <div className="max-h-64 space-y-2 overflow-auto">
      {search.results.map(source => {
        const state = copy?.item === source.item ? copy : null;
        return <article key={`${source.item}:${source.version}`} className="space-y-2 rounded-md border p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0"><h3 className="break-words text-sm font-medium">{source.title}</h3><p className="text-xs text-muted-foreground">Version {source.version} · {source.classification}</p></div>
            <button type="button" disabled={copy?.status === "pending"} onClick={() => void importResult(source)} className={buttonClass}>{state?.status === "pending" ? "Importing..." : state?.status === "error" ? "Retry Import" : "Import"}</button>
          </div>
          <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">{source.snippet}</p>
          {state?.status === "error" && <div role="alert" className="space-y-1 text-sm"><p>{state.message}</p>{state.signIn && <a href={configuration.url} target="_blank" rel="noopener noreferrer" className="underline">Sign in to JupyterLab</a>}</div>}
          {state?.status === "partial" && state.path && <p role="alert" className="break-words text-sm">Personal copy saved at {state.path}. {state.message} {openCopy(state.path)}</p>}
          {state?.status === "done" && state.path && <p role="status" className="break-words text-sm">Imported to {state.path}. {openCopy(state.path)}</p>}
        </article>;
      })}
    </div>}
  </section>;
}
