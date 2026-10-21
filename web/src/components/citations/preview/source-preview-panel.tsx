import { useEffect, useState } from "react";
import { usePersonalChatWorkspace } from "@/components/personal-chat-workspace";
import { useSourceImport, sourceKey } from "@/hooks/use-source-import";
import { personalJupyterURL } from "@/lib/personal-preview";
import type { SourceResult } from "@/lib/source-import";
import { documentSource, type ChatSource } from "@/lib/chat-source-results";
import { loadSourceMarkdown, type SourceMarkdownResult } from "@/lib/source-markdown";
import { documentLabel, documentScope } from "@/lib/document-labels";
import { useMissionTitles } from "@/hooks/use-mission-titles";
import { StatusMessage } from "@/components/ui/status-message";
import { apiFetch, apiResourceURL } from "@/lib/api-client";
import {
  useCitations,
  type PreviewDocument,
} from "@/components/citations/citation-context";
import { DocumentRenderer } from "@/components/citations/preview/document-renderer";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DownloadIcon,
  FolderInputIcon,
  ExternalLinkIcon,
  XIcon,
} from "lucide-react";
import {
  sourcePreviewKey,
  useDocumentPreview,
  useIsDesktopPreview,
  useResizableSourcePanel,
  type DocumentPreview,
} from "@/components/citations/preview/use-source-preview";

export function SourcePreviewPanel() {
  const { selectedSource, closeSource } = useCitations();
  const isDesktop = useIsDesktopPreview();
  const { width, resizeHandleProps } = useResizableSourcePanel();

  if (isDesktop) {
    return selectedSource ? (
      <aside
        className="bg-background relative flex shrink-0 flex-col border-l"
        style={{ width }}
      >
        <div
          {...resizeHandleProps}
          className="hover:bg-primary/30 focus-visible:bg-primary/30 absolute inset-y-0 -left-1 z-10 w-2 cursor-col-resize touch-none outline-none"
          role="separator"
          aria-label="Resize source preview"
          aria-orientation="vertical"
          tabIndex={0}
        />
        <PreviewContent
          key={sourcePreviewKey(selectedSource)}
          source={selectedSource}
          onClose={closeSource}
          inDialog={false}
        />
      </aside>
    ) : null;
  }

  return (
    <Dialog
      open={selectedSource !== null}
      onOpenChange={(open) => {
        if (!open) closeSource();
      }}
    >
      <DialogContent
        className="h-[calc(100dvh-1rem)] max-w-[calc(100%-1rem)] grid-rows-[auto_1fr] overflow-hidden p-0 sm:max-w-[calc(100%-1rem)]"
        showCloseButton={false}
      >
        {selectedSource && (
          <PreviewContent
            key={sourcePreviewKey(selectedSource)}
            source={selectedSource}
            onClose={closeSource}
            inDialog
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function PreviewContent({
  source,
  onClose,
  inDialog,
}: {
  source: PreviewDocument;
  onClose: () => void;
  inDialog: boolean;
}) {
  const preview = useDocumentPreview(source);
  const workspace = usePersonalChatWorkspace();
  const details = usePreviewSource(source);
  const [textMode, setTextMode] = useState<"unrequested" | "text" | "original">("unrequested");
  const fallback = preview.type === "unsupported";
  const showText = textMode === "text" || fallback;
  const markdown = useSourceMarkdown(source, textMode !== "unrequested" || fallback);
  const canReadText = source.sourceID && source.version && (fallback || preview.type === "ready" && ["docx", "xlsx", "pdf"].includes(preview.format));
  const missions = useMissionTitles([details.source?.tags?.mission]);
  const title = details.source ? documentLabel(details.source.title, details.source.tags, missions) : source.title;
  const subtitle = details.source ? documentScope(details.source.version, details.source.classification, details.source.simulated, details.source.tags) : previewSubtitle(source);

  return (
    <>
      <DialogHeader className="relative border-b px-4 py-3 pr-12">
        {inDialog ? (
          <>
            <DialogTitle className="break-words">{title}</DialogTitle>
            <DialogDescription>
              {subtitle}
            </DialogDescription>
          </>
        ) : (
          <>
            <h2 className="break-words text-base font-semibold leading-snug">
              {title}
            </h2>
            <p className="text-muted-foreground text-xs leading-relaxed">
              {subtitle}
            </p>
          </>
        )}
        {source.sourceID && <p className="text-muted-foreground text-xs">Source preview · Read only{source.locationLabel && ` · ${source.locationLabel}`}</p>}
        {details.source?.filename && <p className="text-muted-foreground break-words text-xs">{details.source.filename}{details.source.indexedAt && Number.isFinite(Date.parse(details.source.indexedAt)) && ` · Last indexed ${new Date(details.source.indexedAt).toLocaleDateString()}`}</p>}
        {details.error && <p role="alert" className="text-destructive text-xs">{details.error}</p>}
        {source.sourceID && source.version && workspace?.configuration.status === "configured" && <PreviewImport key={JSON.stringify([source.sourceID, source.version])} verified={details.source} />}
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="absolute right-2 top-2"
          onClick={onClose}
          aria-label="Close source preview"
        >
          <XIcon />
        </Button>
      </DialogHeader>

      <div
        data-source-preview-scroll
        className={
          !showText && preview.type === "ready" && preview.format === "xlsx"
            ? "min-h-0 flex-1 overflow-hidden"
            : !showText && preview.type === "ready" && preview.format === "docx"
              ? "min-h-0 flex-1 overflow-auto"
              : "min-h-0 flex-1 overflow-auto p-4"
        }
      >
        {showText ? <SourceMarkdownBody view={markdown.view} fallback={fallback} /> : <PreviewBody preview={preview} source={source} />}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t p-3">
        <Button
          variant="outline"
          size="sm"
          nativeButton={false}
          render={
            <a href={apiResourceURL(source.previewURL)} target="_blank" rel="noreferrer" />
          }
        >
          <ExternalLinkIcon /> Open original
        </Button>
        <Button
          variant="ghost"
          size="sm"
          nativeButton={false}
          render={<a href={apiResourceURL(source.downloadURL)} download />}
        >
          <DownloadIcon /> Download
        </Button>
        {canReadText && !showText && <Button variant="outline" size="sm" onClick={() => setTextMode("text")}>Read complete indexed text</Button>}
        {showText && preview.type === "ready" && <Button variant="outline" size="sm" onClick={() => setTextMode("original")}>Preview original</Button>}
        {markdown.view.type === "complete" && markdown.url && <Button variant="outline" size="sm" nativeButton={false} render={<a href={markdown.url} download={markdown.view.filename} />}><DownloadIcon /> Download Markdown</Button>}
        {source.version && (
          <span className="text-muted-foreground ml-auto text-xs">
            Version {source.version.slice(0, 12)}
          </span>
        )}
      </div>
    </>
  );
}

function PreviewBody({
  preview,
  source,
}: {
  preview: DocumentPreview;
  source: PreviewDocument;
}) {
  if (preview.type === "loading") {
    return (
      <StatusMessage kind="loading" title="Loading document preview…" />
    );
  }
  if (preview.type === "error") {
    return <StatusMessage kind="error" title={preview.message} detail="Close this preview and open the document again." />;
  }
  if (preview.type === "unsupported") {
    return <StatusMessage kind="empty" title="This original format has no inline preview." detail="Open or download the original document, or read its indexed text when available." />;
  }
  return (
    <DocumentRenderer
      format={preview.format}
      blob={preview.blob}
      source={source}
    />
  );
}

type SourceMarkdownView = SourceMarkdownResult | { type: "loading"; covered: number; total: number };

function SourceMarkdownBody({ view, fallback }: { view: SourceMarkdownView; fallback: boolean }) {
  return <div className="space-y-3">
    {fallback && <StatusMessage kind="empty" title="Original format has no inline renderer" detail="Indexed-text fallback · Open original and Download still provide the original file." />}
    {view.type === "loading" ? <StatusMessage kind="loading" title="Loading complete indexed text…" detail={view.total ? `Verified ${view.covered} of ${view.total} indexed blocks.` : "Checking the selected version and its outline."} /> :
      view.type === "unavailable" ? <StatusMessage kind="error" title={view.covered ? "Partial indexed text is unavailable" : "Complete indexed text is unavailable"} detail={`${view.message}${view.total ? ` Verified ${view.covered} of ${view.total} indexed blocks; complete coverage was not established.` : ""}`} /> :
        <><p className="text-sm font-medium">Complete indexed text · {view.chunks.length}/{view.chunks.length} blocks verified</p><p className="text-xs text-muted-foreground">All indexed text; layout/images may be omitted. This Markdown reconstructs extracted text for the selected version.</p><pre aria-label="Complete indexed document Markdown" className="whitespace-pre-wrap break-words text-sm leading-relaxed">{view.markdown}</pre></>}
  </div>;
}

function useSourceMarkdown(source: PreviewDocument, active: boolean) {
  const { refuseSource } = useCitations();
  const key = sourcePreviewKey(source);
  const [state, setState] = useState<{ key: string; view: SourceMarkdownView; url?: string } | null>(null);
  useEffect(() => {
    if (!active || !source.sourceID || !source.version) return;
    const controller = new AbortController();
    let objectURL: string | undefined;
    void loadSourceMarkdown(source.sourceID, Number(source.version), controller.signal, progress => {
      if (!controller.signal.aborted) setState({ key, view: { type: "loading", ...progress } });
    }).then(view => {
      if (controller.signal.aborted) return;
      if (view.type === "unavailable" && view.refused) refuseSource(source);
      if (view.type === "complete") objectURL = URL.createObjectURL(new Blob([view.markdown], { type: "text/markdown;charset=utf-8" }));
      setState({ key, view, url: objectURL });
    });
    return () => { controller.abort(); if (objectURL) URL.revokeObjectURL(objectURL); };
  }, [active, key, source, refuseSource]);
  return active && state?.key === key ? state : { view: { type: "loading" as const, covered: 0, total: 0 }, url: undefined };
}

function previewSubtitle(source: PreviewDocument): string {
  return source.subtitle ?? source.locationLabel ?? source.locator ?? "";
}

function usePreviewSource(source: PreviewDocument) {
  const [details, setDetails] = useState<{ source?: ChatSource; error?: string }>({});
  useEffect(() => {
    if (!source.sourceID || !source.version) return;
    const controller = new AbortController();
    const unavailable = () => setDetails({ error: "Source details could not be confirmed. Close the preview and try again." });
    const timer = setTimeout(() => { unavailable(); controller.abort(); }, 10000);
    const version = Number(source.version);
    void apiFetch(`/api/library/documents/${encodeURIComponent(source.sourceID)}?version=${version}`, { signal: controller.signal, cache: "no-store" }).then(async response => {
      if (!response.ok) throw new Error();
      const verified = documentSource(await response.json());
      if (!verified || verified.item !== source.sourceID || verified.version !== version) throw new Error();
      if (!controller.signal.aborted) setDetails({ source: verified });
    }).catch(() => { if (!controller.signal.aborted) unavailable(); }).finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); controller.abort(); };
  }, [source]);
  return details;
}

function PreviewImport({ verified }: { verified?: SourceResult }) {
  const workspace = usePersonalChatWorkspace();
  const { state, start, reset } = useSourceImport(workspace?.configuration);
  const configuration = workspace?.configuration;
  if (configuration?.status !== "configured") return null;
  const current = verified ? state.get(sourceKey(verified)) : undefined;
  if (current?.status === "done" || current?.status === "partial") return <div role={current.status === "done" ? "status" : "alert"} className="mt-2 flex flex-wrap items-center gap-2 text-xs">
    <span>Imported as {current.path}</span>
    {current.message && <span>{current.message}</span>}
    {current.path && <a className="underline" href={personalJupyterURL(configuration, current.path)} target="_blank" rel="noopener noreferrer">Open</a>}
    {current.status === "done" && <button type="button" className="underline" onClick={() => { reset(verified!); void start(verified!); }}>Import another copy</button>}
  </div>;
  return <div className="mt-2 space-y-1 text-xs">
    <Button size="sm" variant="outline" disabled={!verified || current?.status === "pending" || current?.status === "unknown"} onClick={() => verified && void start(verified)}><FolderInputIcon />{current?.status === "pending" ? "Importing…" : "Import to workspace"}</Button>
    {current?.message && <p role="alert">{current.message}{current?.path && ` Check ${current.path}.`}</p>}
    {current?.signIn && <a className="underline" href={configuration.url} target="_blank" rel="noopener noreferrer">Sign in to workspace</a>}
  </div>;
}
