import { useEffect, useRef, useState } from "react";
import { ChevronLeft, File, FileCode, FileSpreadsheet, FileText, Folder, Notebook } from "lucide-react";
import { DocumentRenderer } from "@/components/citations/preview/document-renderer";
import type { DocumentFormat } from "@/components/citations/preview/renderer-contract";
import type { WorkspaceConfiguration } from "@/components/workspace-url";
import { listPersonalDirectory, PersonalWorkspaceError, personalAbsolutePath, readPersonalFile, type PersonalEntry } from "@/lib/personal-workspace";
import { personalFileFormat, personalJupyterURL } from "@/lib/personal-preview";

const buttonClass = "rounded border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50";
export function PersonalFileBrowser({ configuration, onOpenFile, refreshToken = 0, revealFile }: { configuration: WorkspaceConfiguration; onOpenFile: (path: string) => void; refreshToken?: number; revealFile?: string }) {
  if (configuration.status !== "configured") return <div className="p-4 text-sm">Connect your personal workspace to browse files.</div>;
  return <ConnectedBrowser key={`${configuration.url}:${revealFile ? `${refreshToken}:${revealFile}` : "browse"}`} configuration={configuration} onOpenFile={onOpenFile} refreshToken={refreshToken} revealFile={revealFile} />;
}
function ConnectedBrowser({ configuration, onOpenFile, refreshToken, revealFile }: { configuration: WorkspaceConfiguration & { status: "configured" }; onOpenFile: (path: string) => void; refreshToken: number; revealFile?: string }) {
  const [path, setPath] = useState(() => revealFile?.split("/").slice(0, -1).join("/") ?? "");
  const [refresh, setRefresh] = useState(0), [query, setQuery] = useState("");
  const [selectedPath, setSelectedPath] = useState(revealFile);
  const filterInput = useRef<HTMLInputElement>(null);
  const [loaded, setLoaded] = useState<{ key: string; files?: PersonalEntry[]; error?: string; signIn?: boolean } | null>(null);
  const key = JSON.stringify([path, refresh, refreshToken]);
  useEffect(() => {
    const controller = new AbortController();
    void listPersonalDirectory(configuration, path, controller.signal).then(files => { if (!controller.signal.aborted) setLoaded({ key, files }); }).catch(reason => { if (!controller.signal.aborted) setLoaded({ key, error: reason instanceof PersonalWorkspaceError ? reason.message : "Could not read personal files.", signIn: reason instanceof PersonalWorkspaceError && reason.code === "workspace_auth" }); });
    return () => controller.abort();
  }, [configuration, path, key]);
  const current = loaded?.key === key ? loaded : null;
  const files = current?.files?.filter(file => file.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) ?? [];
  const navigate = (next: string) => { setPath(next); setQuery(""); setSelectedPath(undefined); };
  return <aside aria-label="Personal files" className="flex min-h-0 min-w-0 w-64 max-w-[42%] shrink-0 flex-col border-r bg-muted/10 lg:w-72 xl:w-80">
    <div className="shrink-0 space-y-3 p-3">
      <div className="flex items-center justify-between gap-2"><h2 className="text-sm font-medium">Personal files</h2><button type="button" className={buttonClass} onClick={() => setRefresh(value => value + 1)} disabled={!current}>Refresh</button></div>
      <p className="text-xs text-muted-foreground">USER_ROOT <code>/workspace</code></p>
      <nav aria-label="Personal folder" className="flex flex-wrap items-center gap-2"><button type="button" aria-label="Go to workspace root" className={buttonClass} onClick={() => navigate("")}>/workspace</button>{path && <button type="button" aria-label="Back to parent folder" className={`${buttonClass} inline-flex items-center gap-1`} onClick={() => navigate(path.split("/").slice(0, -1).join("/"))}><ChevronLeft aria-hidden="true" className="size-3" />Back</button>}</nav>
      <p aria-label="Current folder" className="break-words font-mono text-xs">{path ? personalAbsolutePath(path) : "/workspace"}</p>
      <input ref={filterInput} aria-label="Filter personal files" type="search" className="w-full min-w-0 rounded border bg-background p-2 text-sm" value={query} onInput={event => setQuery(event.currentTarget.value)} placeholder="Filter this folder" />
    </div>
    <div className="min-h-0 flex-1 overflow-auto px-3 pb-3">
      {!current && <p role="status" className="text-sm">Reading personal files…</p>}
      {current?.error && <div className="space-y-2"><p role="alert" className="text-sm">{current.error}</p><div className="flex flex-wrap items-center gap-2"><button type="button" className={buttonClass} onClick={() => setRefresh(value => value + 1)}>Retry</button>{current.signIn && <a href={configuration.url} target="_blank" rel="noopener noreferrer" className="text-xs underline">Sign in to JupyterLab ↗</a>}</div></div>}
      {files.length > 0 && <ul className="space-y-1">{files.map(file => {
        const format = personalFileFormat(file.name);
        const opensInJupyter = file.type === "notebook" || file.name.toLowerCase().endsWith(".py") || !format;
        const Icon = file.type === "directory" ? Folder : file.type === "notebook" ? Notebook : file.name.toLowerCase().endsWith(".py") ? FileCode : format === "xlsx" ? FileSpreadsheet : format ? FileText : File;
        const content = <><Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" /><span className="min-w-0"><span className="block break-words">{file.name}</span><span className="block text-xs text-muted-foreground">{file.type === "directory" ? "Folder" : opensInJupyter ? "Open in JupyterLab ↗" : "Preview and download"}</span></span></>;
        const rowClass = `flex w-full items-start gap-2 rounded-md px-2 py-3 text-left text-sm hover:bg-muted ${selectedPath === file.path ? "bg-muted" : ""}`;
        return <li key={file.path}>{file.type === "directory" ? <button type="button" aria-label={`Open folder ${file.name}`} className={rowClass} onClick={() => navigate(file.path)}>{content}</button> : opensInJupyter ? <a href={personalJupyterURL(configuration, file.path)} target="_blank" rel="noopener noreferrer" aria-current={selectedPath === file.path ? "true" : undefined} className={rowClass} onClick={() => setSelectedPath(file.path)}>{content}</a> : <button type="button" aria-label={`Preview ${file.name}`} aria-current={selectedPath === file.path ? "true" : undefined} className={rowClass} onClick={() => { setSelectedPath(file.path); onOpenFile(file.path); }}>{content}</button>}</li>;
      })}</ul>}
      {current?.files?.length === 0 && <p className="text-sm text-muted-foreground">This folder is empty.</p>}
      {!!current?.files?.length && files.length === 0 && <div className="space-y-2"><p role="status" className="break-words text-sm text-muted-foreground">No files or folders match “{query.trim()}”.</p><button type="button" className={buttonClass} onClick={() => { setQuery(""); filterInput.current?.focus(); }}>Clear filter</button></div>}
    </div>
    <p className="shrink-0 border-t p-3 text-xs text-muted-foreground">Preview and download personal copies here. Edit scripts and notebooks in JupyterLab.</p>
  </aside>;
}

export function PersonalFilePreview({ configuration, selection, onClose }: { configuration: WorkspaceConfiguration & { status: "configured" }; selection: { path: string }; onClose: () => void }) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const [loaded, setLoaded] = useState<{ selection: { path: string }; blob?: Blob; url?: string; format?: DocumentFormat; error?: string } | null>(null);
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButton.current?.focus();
    return () => { if (opener?.isConnected && !opener.closest("[hidden], [inert]")) opener.focus(); };
  }, []);
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
  return <div role="dialog" aria-modal="true" aria-label={`Personal preview ${name}`} className="absolute inset-0 z-50 flex min-h-0 flex-col bg-background" onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
    if (event.key === "Tab") {
      const controls = [...event.currentTarget.querySelectorAll<HTMLElement>("button, a[href], input, select, textarea, [tabindex]")].filter(control => !control.hasAttribute("disabled") && control.getAttribute("tabindex") !== "-1");
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) { event.preventDefault(); (event.shiftKey ? last : first)?.focus(); }
    }
  }}>
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b p-3"><div className="min-w-0"><h2 className="break-words text-sm font-medium">{name}</h2><p className="break-words font-mono text-xs text-muted-foreground">{personalAbsolutePath(selection.path)}</p></div><div className="flex gap-2">
      {current?.url && <a href={current.url} download={name} className={buttonClass}>Download</a>}
      <a href={personalJupyterURL(configuration, selection.path)} target="_blank" rel="noopener noreferrer" className={buttonClass}>Open in JupyterLab ↗</a><button ref={closeButton} type="button" aria-label="Close personal preview" className={buttonClass} onClick={onClose}>Close</button>
    </div></header>
    <div data-source-preview-scroll className="min-h-0 flex-1 overflow-auto p-3">
      {!current && <p role="status" className="text-sm">Opening personal file…</p>}
      {current?.error && <p role="alert" className="text-sm">{current.error}</p>}
      {current?.blob && current.url && (current.format ? <DocumentRenderer key={current.url} format={current.format} blob={current.blob} source={{ title: name, mediaType: current.blob.type, previewURL: "", downloadURL: current.url }} /> : <p className="text-sm">Preview is unavailable for this format. Download the personal copy or open it in JupyterLab.</p>)}
    </div>
  </div>;
}
