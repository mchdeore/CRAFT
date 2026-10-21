import { useEffect, useState } from "react";
import { usePersonalChatWorkspace } from "@/components/personal-chat-workspace";
import { useSourceImport, sourceKey } from "@/hooks/use-source-import";
import { personalJupyterURL } from "@/lib/personal-preview";
import type { SourceResult } from "@/lib/source-import";
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
  LoaderCircleIcon,
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

  return (
    <>
      <DialogHeader className="relative border-b px-4 py-3 pr-12">
        {inDialog ? (
          <>
            <DialogTitle className="truncate">{source.title}</DialogTitle>
            <DialogDescription className="truncate">
              {previewSubtitle(source)}
            </DialogDescription>
          </>
        ) : (
          <>
            <h2 className="truncate text-base font-medium leading-none">
              {source.title}
            </h2>
            <p className="text-muted-foreground truncate text-sm">
              {previewSubtitle(source)}
            </p>
          </>
        )}
        {source.sourceID && source.version && workspace?.configuration.status === "configured" && <PreviewImport key={JSON.stringify([source.sourceID, source.version])} source={source} />}
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
      <div className="text-muted-foreground flex h-full items-center justify-center gap-2 text-sm">
        <LoaderCircleIcon className="size-4 animate-spin" /> Loading preview…
      </div>
    );
  }
  if (preview.type === "error") {
    return <p className="text-destructive text-sm">{preview.message}</p>;
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

function PreviewImport({ source }: { source: PreviewDocument }) {
  const workspace = usePersonalChatWorkspace();
  const { state, start, reset } = useSourceImport(workspace?.configuration);
  const [verified, setVerified] = useState<SourceResult>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => { setError("Source details could not be confirmed. Close the preview and try again."); controller.abort(); }, 10000);
    const version = Number(source.version);
    if (source.classification && Number.isSafeInteger(version) && version > 0) {
      Promise.resolve().then(() => { if (!controller.signal.aborted) setVerified({ item: source.sourceID!, version, title: source.title, classification: source.classification!, snippet: "" }); });
    } else {
      void apiFetch(`/api/library/documents/${encodeURIComponent(source.sourceID!)}?version=${version}`, { signal: controller.signal, cache: "no-store" }).then(async response => {
        if (!response.ok) throw new Error();
        const body = await response.json();
        if (body.item !== source.sourceID || body.version !== version || typeof body.title !== "string" || !["unclassified", "protected-a", "protected-b"].includes(body.classification)) throw new Error();
        if (!controller.signal.aborted) setVerified({ item: body.item, version, title: body.title, classification: body.classification, snippet: "" });
      }).catch(() => { if (!controller.signal.aborted) setError("Source details could not be confirmed. Close the preview and try again."); }).finally(() => clearTimeout(timer));
    }
    return () => { clearTimeout(timer); controller.abort(); };
  }, [source]);
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
    {(error || current?.message) && <p role="alert">{error ?? current?.message}{current?.path && ` Check ${current.path}.`}</p>}
    {current?.signIn && <a className="underline" href={configuration.url} target="_blank" rel="noopener noreferrer">Sign in to workspace</a>}
  </div>;
}
