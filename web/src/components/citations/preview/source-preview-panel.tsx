import { useEffect, useState } from "react";
import { usePersonalChatWorkspace } from "@/components/personal-chat-workspace";
import { useSourceImport, sourceKey } from "@/hooks/use-source-import";
import { personalJupyterURL } from "@/lib/personal-preview";
import type { SourceResult } from "@/lib/source-import";
import { documentSource, type ChatSource } from "@/lib/chat-source-results";
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
          preview.type === "ready" && preview.format === "xlsx"
            ? "min-h-0 flex-1 overflow-hidden"
            : preview.type === "ready" && preview.format === "docx"
              ? "min-h-0 flex-1 overflow-auto"
              : "min-h-0 flex-1 overflow-auto p-4"
        }
      >
        <PreviewBody preview={preview} source={source} />
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
  return (
    <DocumentRenderer
      format={preview.format}
      blob={preview.blob}
      source={source}
    />
  );
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
