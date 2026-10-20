import { useEffect, useState } from "react";
import { DocumentRenderer } from "@/components/citations/preview/document-renderer";
import type { DocumentFormat } from "@/components/citations/preview/renderer-contract";
import type { WorkspaceConfiguration } from "@/components/workspace-url";
import { listPersonalDirectory, PersonalWorkspaceError, personalAbsolutePath, readPersonalFile, type PersonalEntry } from "@/lib/personal-workspace";
import { personalFileFormat, personalJupyterURL } from "@/lib/personal-preview";

const buttonClass = "rounded border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50";
export function PersonalFileBrowser({ configuration, onOpenFile, refreshToken = 0 }: { configuration: WorkspaceConfiguration; onOpenFile: (path: string) => void; refreshToken?: number }) {
  if (configuration.status !== "configured") return <div className="p-4 text-sm">Connect your personal workspace to browse files.</div>;
  return <ConnectedBrowser key={configuration.url} configuration={configuration} onOpenFile={onOpenFile} refreshToken={refreshToken} />;
}
function ConnectedBrowser({ configuration, onOpenFile, refreshToken }: { configuration: WorkspaceConfiguration & { status: "configured" }; onOpenFile: (path: string) => void; refreshToken: number }) {
  const [path, setPath] = useState(""); const [refresh, setRefresh] = useState(0), [query, setQuery] = useState("");
  const [loaded, setLoaded] = useState<{ key: string; files?: PersonalEntry[]; error?: string } | null>(null);
  const key = JSON.stringify([path, refresh, refreshToken]);
  useEffect(() => {
    const controller = new AbortController();
    void listPersonalDirectory(configuration, path, controller.signal).then(files => { if (!controller.signal.aborted) setLoaded({ key, files }); }).catch(reason => { if (!controller.signal.aborted) setLoaded({ key, error: reason instanceof PersonalWorkspaceError ? reason.message : "Could not read personal files." }); });
    return () => controller.abort();
  }, [configuration, path, key]);
  const current = loaded?.key === key ? loaded : null;
  return <aside aria-label="Personal files" className="flex w-56 shrink-0 flex-col gap-3 overflow-auto border-r bg-muted/10 p-3 lg:w-64">
    <div className="flex items-center justify-between gap-2"><h2 className="text-sm font-medium">Personal files</h2><button type="button" className={buttonClass} onClick={() => setRefresh(value => value + 1)} disabled={!current}>Refresh</button></div>
    <nav aria-label="Personal folder" className="flex flex-wrap items-center gap-2"><button type="button" className={buttonClass} onClick={() => { setPath(""); setQuery(""); }}>/workspace</button>{path && <button type="button" className={buttonClass} onClick={() => { setPath(path.split("/").slice(0, -1).join("/")); setQuery(""); }}>Up</button>}</nav>
    {path && <p className="break-words font-mono text-xs">{personalAbsolutePath(path)}</p>}
    <input aria-label="Filter personal files" type="search" className="min-w-0 rounded border bg-background p-2 text-sm" value={query} onInput={event => setQuery(event.currentTarget.value)} placeholder="Filter this folder" />
    {!current && <p role="status" className="text-sm">Reading personal files…</p>}
    {current?.error && <p role="alert" className="text-sm">{current.error}</p>}
    {current?.files && <ul className="space-y-1">{current.files.filter(file => file.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())).map(file => <li key={file.path}>
      {file.type === "directory" ? <button type="button" aria-label={`Open folder ${file.name}`} className="w-full break-words rounded px-2 py-2 text-left text-sm hover:bg-muted" onClick={() => { setPath(file.path); setQuery(""); }}>▸ {file.name}</button> : file.type === "notebook" || file.name.endsWith(".py") || !personalFileFormat(file.name) ? <a href={personalJupyterURL(configuration, file.path)} target="_blank" rel="noopener noreferrer" className="block break-words rounded px-2 py-2 text-sm hover:bg-muted">{file.name}<span className="block text-xs text-muted-foreground">Open in JupyterLab ↗</span></a> : <button type="button" aria-label={`Preview ${file.name}`} className="w-full break-words rounded px-2 py-2 text-left text-sm hover:bg-muted" onClick={() => onOpenFile(file.path)}>{file.name}<span className="block text-xs text-muted-foreground">Preview and download</span></button>}
    </li>)}</ul>}
    {current?.files?.length === 0 && <p className="text-sm text-muted-foreground">This folder is empty.</p>}
    <p className="text-xs text-muted-foreground">Word and Excel copies open here. Use JupyterLab to edit scripts and notebooks.</p>
  </aside>;
}

export function PersonalFilePreview({ configuration, selection, onClose }: { configuration: WorkspaceConfiguration & { status: "configured" }; selection: { path: string }; onClose: () => void }) {
  const [loaded, setLoaded] = useState<{ selection: { path: string }; blob?: Blob; url?: string; format?: DocumentFormat; error?: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController(); let objectURL: string | undefined;
    void readPersonalFile(configuration, selection.path, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      const blob = new Blob([result.bytes], { type: result.mimeType }); objectURL = URL.createObjectURL(blob);
      setLoaded({ selection, blob, url: objectURL, format: personalFileFormat(selection.path) });
    }).catch(reason => { if (!controller.signal.aborted) setLoaded({ selection, error: reason instanceof PersonalWorkspaceError ? reason.message : "Could not open this personal file." }); });
    return () => { controller.abort(); if (objectURL) URL.revokeObjectURL(objectURL); };
  }, [configuration, selection]);
  const current = loaded?.selection === selection ? loaded : null;
  const name = selection.path.split("/").at(-1)!;
  return <div role="dialog" aria-modal="true" aria-label={`Personal preview ${name}`} className="absolute inset-0 z-50 flex min-h-0 flex-col bg-background" onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); onClose(); } }}>
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b p-3"><div className="min-w-0"><h2 className="break-words text-sm font-medium">{name}</h2><p className="break-words font-mono text-xs text-muted-foreground">{personalAbsolutePath(selection.path)}</p></div><div className="flex gap-2">
      {current?.url && <a href={current.url} download={name} className={buttonClass}>Download</a>}
      <a href={personalJupyterURL(configuration, selection.path)} target="_blank" rel="noopener noreferrer" className={buttonClass}>Open in JupyterLab ↗</a><button type="button" aria-label="Close personal preview" className={buttonClass} onClick={onClose}>Close</button>
    </div></header>
    <div data-source-preview-scroll className="min-h-0 flex-1 overflow-auto p-3">
      {!current && <p role="status" className="text-sm">Opening personal file…</p>}
      {current?.error && <p role="alert" className="text-sm">{current.error}</p>}
      {current?.blob && current.url && (current.format ? <DocumentRenderer key={current.url} format={current.format} blob={current.blob} source={{ title: name, mediaType: current.blob.type, previewURL: "", downloadURL: current.url }} /> : <p className="text-sm">Preview is unavailable for this format. Download the personal copy or open it in JupyterLab.</p>)}
    </div>
  </div>;
}
