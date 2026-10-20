import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ComponentProps, type PropsWithChildren } from "react";
import { CitationLink } from "@/components/citations/citation-link";
import { DocumentRenderer } from "@/components/citations/preview/document-renderer";
import type { WorkspaceConfiguration } from "@/components/workspace-url";
import { personalChatPreview, personalWorkspaceLinkPath, type PersonalChatPreview } from "@/lib/chat-workspace-preview";
import { personalAbsolutePath, PersonalWorkspaceError, readPersonalFile } from "@/lib/personal-workspace";
import { cn } from "@/lib/utils";

type Selection = { path: string };
interface WorkspacePreviewContext {
  configuration: WorkspaceConfiguration;
  selection: Selection | null;
  open: (path: string) => void;
  close: () => void;
}
const PreviewContext = createContext<WorkspacePreviewContext | null>(null);

export function PersonalChatWorkspaceProvider({ configuration, ownerKey, children }: PropsWithChildren<{ configuration?: WorkspaceConfiguration; ownerKey: string }>) {
  const url = configuration?.status === "configured" ? configuration.url : undefined;
  const key = JSON.stringify([ownerKey, url ?? configuration?.status ?? "missing"]);
  return <ScopedWorkspacePreview key={key} url={url}>{children}</ScopedWorkspacePreview>;
}
function ScopedWorkspacePreview({ url, children }: PropsWithChildren<{ url?: string }>) {
  const configuration = useMemo<WorkspaceConfiguration>(() => url ? { status: "configured", url } : { status: "missing" }, [url]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const open = useCallback((path: string) => { personalAbsolutePath(path); setSelection({ path }); }, []);
  const close = useCallback(() => setSelection(null), []);
  const value = useMemo(() => ({ configuration, selection, open, close }), [configuration, selection, open, close]);
  return <PreviewContext.Provider value={value}>{children}</PreviewContext.Provider>;
}

export function PersonalWorkspaceMarkdownLink({ href, className, onClick, children, ...props }: ComponentProps<"a">) {
  const workspace = useContext(PreviewContext);
  const path = personalWorkspaceLinkPath(href);
  if (path && workspace) return <a {...props} href={href} className={cn("text-primary hover:text-primary/80 underline underline-offset-2", className)} aria-controls="personal-chat-preview" onClick={event => {
    onClick?.(event);
    if (!event.defaultPrevented) { event.preventDefault(); workspace.open(path); }
  }}>{children}</a>;
  if (!path && (href === "/workspace" || href?.startsWith("/workspace/"))) return <span className={cn("text-muted-foreground", className)} title="This workspace path cannot be previewed.">{children}</span>;
  return <CitationLink {...props} href={href} className={className} onClick={onClick}>{children}</CitationLink>;
}

export function PersonalChatPreviewPanel() {
  const workspace = useContext(PreviewContext);
  if (!workspace?.selection) return null;
  return <WorkspacePreviewContent configuration={workspace.configuration} selection={workspace.selection} onClose={workspace.close} />;
}

const buttonClass = "rounded border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50";
type LoadedPreview = { selection: Selection; refresh: number; blob?: Blob; url?: string; preview?: PersonalChatPreview; error?: string };
function WorkspacePreviewContent({ configuration, selection, onClose }: { configuration: WorkspaceConfiguration; selection: Selection; onClose: () => void }) {
  const [refresh, setRefresh] = useState(0);
  const [loaded, setLoaded] = useState<LoadedPreview | null>(null);
  useEffect(() => {
    if (configuration.status !== "configured") return;
    const controller = new AbortController(); let objectURL: string | undefined;
    void readPersonalFile(configuration, selection.path, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      const blob = new Blob([result.bytes], { type: result.mimeType });
      objectURL = URL.createObjectURL(blob);
      let preview: PersonalChatPreview | undefined, error: string | undefined;
      try { preview = personalChatPreview(selection.path, result.bytes); }
      catch (reason) { error = reason instanceof Error ? reason.message : "This file could not be previewed."; }
      setLoaded({ selection, refresh, blob, url: objectURL, preview, error });
    }).catch(reason => {
      if (!controller.signal.aborted) setLoaded({ selection, refresh, error: reason instanceof PersonalWorkspaceError ? reason.message : "Could not open this workspace file." });
    });
    return () => { controller.abort(); if (objectURL) URL.revokeObjectURL(objectURL); };
  }, [configuration, selection, refresh]);
  const current = loaded?.selection === selection && loaded.refresh === refresh ? loaded : null;
  const connected = configuration.status === "configured";
  const name = selection.path.split("/").at(-1)!;
  return <section id="personal-chat-preview" aria-label="Workspace file preview" className="flex h-[min(45dvh,28rem)] shrink-0 flex-col border-t bg-background" onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); onClose(); }
  }}>
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b p-3">
      <div className="min-w-0"><h2 className="break-words text-sm font-medium">{name}</h2><p className="break-words font-mono text-xs text-muted-foreground">{personalAbsolutePath(selection.path)}</p><p className="text-xs text-muted-foreground">Current workspace file · refresh to read saved changes</p></div>
      <div className="flex flex-wrap gap-2">
        {current?.url && <a href={current.url} download={name} className={buttonClass}>Download</a>}
        {connected && <button type="button" aria-label={current?.error ? "Retry workspace preview" : "Refresh workspace preview"} className={buttonClass} disabled={!current} onClick={() => setRefresh(value => value + 1)}>{current?.error ? "Retry" : "Refresh"}</button>}
        <button type="button" aria-label="Close workspace preview" className={buttonClass} onClick={onClose}>Close</button>
      </div>
    </header>
    <div data-source-preview-scroll className="min-h-0 flex-1 overflow-auto p-3">
      {!connected ? <p role="status" className="text-sm">Connect your personal workspace to preview this file.</p> : !current ? <p role="status" className="text-sm">Opening workspace file…</p> : <>
        {current.error && <p role="alert" className="text-sm">{current.error}</p>}
        {current.preview && current.blob && current.url && <PreviewBody preview={current.preview} blob={current.blob} url={current.url} name={name} />}
      </>}
    </div>
  </section>;
}

function PreviewBody({ preview, blob, url, name }: { preview: PersonalChatPreview; blob: Blob; url: string; name: string }) {
  if (preview.kind === "document") return <DocumentRenderer key={url} format={preview.format} blob={blob} source={{ title: name, mediaType: blob.type, previewURL: "", downloadURL: url }} />;
  if (preview.kind === "unsupported") return <p role="status" className="text-sm">Preview is unavailable for this format. Download the current workspace file to inspect it.</p>;
  if (preview.kind === "code") return <><p className="mb-2 text-xs font-medium">{preview.language}</p><pre className="overflow-auto whitespace-pre font-mono text-sm leading-relaxed"><code>{preview.text}</code></pre>{preview.truncated && <TruncatedPreview />}</>;
  return <div className="space-y-4">
    {preview.cells.length === 0 && <p className="text-sm text-muted-foreground">This notebook has no cells.</p>}
    {preview.cells.map((cell, index) => <article key={index} className="rounded border p-3">
      <h3 className="mb-2 text-xs font-medium">Cell {index + 1} · {cell.type}</h3>
      <pre className="overflow-auto whitespace-pre font-mono text-sm leading-relaxed"><code>{cell.source}</code></pre>
      {cell.outputs.map((output, outputIndex) => <pre key={outputIndex} aria-label={`Cell ${index + 1} text output ${outputIndex + 1}`} className="mt-2 overflow-auto whitespace-pre-wrap break-words border-l-2 pl-3 font-mono text-xs leading-relaxed">{output}</pre>)}
      {cell.unsupportedOutputs > 0 && <p className="mt-2 text-xs text-muted-foreground">Rich outputs are omitted. This preview shows cell source and plain text outputs.</p>}
      {cell.truncated && <p className="mt-2 text-xs text-muted-foreground">Cell preview truncated.</p>}
    </article>)}
    {preview.truncated && <TruncatedPreview />}
  </div>;
}
function TruncatedPreview() { return <p className="mt-3 text-xs text-muted-foreground">Preview truncated. Download the current workspace file to inspect all content.</p>; }
