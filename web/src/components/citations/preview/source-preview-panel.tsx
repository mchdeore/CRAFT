import { apiResourceURL } from "@/lib/api-client";
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
