import { OfficePreviewToolbar } from "@/components/citations/preview/office-preview-toolbar";
import type { DocumentRendererProps } from "@/components/citations/preview/renderer-contract";
import { Button } from "@/components/ui/button";
import { StatusMessage } from "@/components/ui/status-message";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  FileTextIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

export function DocxRenderer({ blob, source }: DocumentRendererProps) {
  const { locationLabel } = source;
  const documentRef = useRef<HTMLDivElement>(null);
  const citationTargetRef = useRef<HTMLElement | undefined>(undefined);
  const [error, setError] = useState<string>();
  const [rendering, setRendering] = useState(true);
  const [pageCount, setPageCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [zoom, setZoom] = useState(1);

  const pages = useCallback(
    () => [
      ...(documentRef.current?.querySelectorAll<HTMLElement>(
        "section.docx",
      ) ?? []),
    ],
    [],
  );

  const goToPage = useCallback(
    (pageNumber: number) => {
      const targetPage = pages()[pageNumber - 1];
      if (!targetPage) return;
      setCurrentPage(pageNumber);
      targetPage.scrollIntoView({ block: "start", behavior: "smooth" });
    },
    [pages],
  );

  const fitWidth = useCallback(() => {
    const document = documentRef.current;
    const firstPage = pages()[0];
    const viewport = document?.closest<HTMLElement>(
      "[data-source-preview-scroll]",
    );
    if (!document || !firstPage || !viewport) return;
    const availableWidth = Math.max(1, viewport.clientWidth - 32);
    setZoom(fitZoom(availableWidth / firstPage.offsetWidth));
  }, [pages]);

  useEffect(() => {
    const container = documentRef.current;
    if (!container) return;

    let isCurrent = true;
    setError(undefined);
    setRendering(true);
    setPageCount(0);
    setCurrentPage(1);
    container.replaceChildren();
    citationTargetRef.current = undefined;

    void import("docx-preview")
      .then(({ renderAsync }) =>
        renderAsync(blob, container, undefined, {
          breakPages: true,
          experimental: true,
          ignoreLastRenderedPageBreak: false,
          renderAltChunks: false,
          renderChanges: true,
          renderComments: true,
          renderFootnotes: true,
          renderHeaders: true,
          renderFooters: true,
        }),
      )
      .then(() => {
        if (!isCurrent) return;
        const renderedPages = pages();
        setPageCount(renderedPages.length);
        setRendering(false);
        if (locationLabel?.startsWith("§ ")) {
          const heading = locationLabel.slice(2).trim();
          const target = [
            ...container.querySelectorAll<HTMLElement>(
              "h1,h2,h3,h4,h5,h6,p",
            ),
          ].find((element) => element.textContent?.trim() === heading);
          if (target) {
            citationTargetRef.current = target;
            highlightCitation(target);
            const pageIndex = renderedPages.findIndex((page) =>
              page.contains(target),
            );
            if (pageIndex >= 0) setCurrentPage(pageIndex + 1);
          }
        }
      })
      .catch(() => {
        if (isCurrent) {
          setRendering(false);
          setError("Word preview unavailable");
        }
      });

    return () => {
      isCurrent = false;
      container.replaceChildren();
    };
  }, [blob, locationLabel, pages]);

  useEffect(() => {
    if (rendering || pageCount === 0) return;
    const frame = requestAnimationFrame(() => {
      fitWidth();
      requestAnimationFrame(() =>
        citationTargetRef.current?.scrollIntoView({ block: "center" }),
      );
    });
    return () => cancelAnimationFrame(frame);
  }, [fitWidth, pageCount, rendering]);

  useEffect(() => {
    const document = documentRef.current;
    const viewport = document?.closest<HTMLElement>(
      "[data-source-preview-scroll]",
    );
    const renderedPages = pages();
    if (!document || !viewport || renderedPages.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort(
            (left, right) => right.intersectionRatio - left.intersectionRatio,
          )[0];
        if (!visible) return;
        const pageIndex = renderedPages.indexOf(visible.target as HTMLElement);
        if (pageIndex >= 0) setCurrentPage(pageIndex + 1);
      },
      { root: viewport, threshold: [0.1, 0.3, 0.6] },
    );
    renderedPages.forEach((page) => observer.observe(page));
    return () => observer.disconnect();
  }, [pageCount, pages, zoom]);

  return (
    <div className="min-h-full min-w-full bg-[#e7e9ed] dark:bg-zinc-950">
      <div className="sticky top-0 z-30 min-w-max">
        <OfficePreviewToolbar
          application="Word"
          color="#185abd"
          icon={<FileTextIcon className="size-4" />}
          zoom={zoom}
          onZoomChange={setZoom}
          onFitWidth={fitWidth}
        >
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            disabled={currentPage <= 1}
            onClick={() => goToPage(currentPage - 1)}
            aria-label="Previous page"
          >
            <ChevronLeftIcon />
          </Button>
          <span className="w-20 text-center text-xs tabular-nums">
            {pageCount > 0 ? `${currentPage} / ${pageCount}` : "— / —"}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            disabled={pageCount === 0 || currentPage >= pageCount}
            onClick={() => goToPage(currentPage + 1)}
            aria-label="Next page"
          >
            <ChevronRightIcon />
          </Button>
        </OfficePreviewToolbar>
      </div>

      {rendering && !error && (
        <div className="flex h-48 items-center justify-center p-4">
          <StatusMessage
            kind="loading"
            title="Opening Word document…"
            detail="You can use Open original or Download while the preview opens."
          />
        </div>
      )}
      {error && (
        <div className="p-4">
          <StatusMessage
            kind="error"
            title={error}
            detail="Use Open original or Download to view this document."
          />
        </div>
      )}
      <div
        ref={documentRef}
        style={{ zoom }}
        className={
          rendering || error
            ? "hidden"
            : "[&_.docx-wrapper]:min-h-full [&_.docx-wrapper]:bg-[#e7e9ed] [&_.docx-wrapper]:px-5 [&_.docx-wrapper]:py-6 dark:[&_.docx-wrapper]:bg-zinc-950 [&_section.docx]:mx-auto [&_section.docx]:mb-6 [&_section.docx]:scroll-mt-14 [&_section.docx]:shadow-lg"
        }
      />
    </div>
  );
}

function highlightCitation(target: HTMLElement): void {
  target.style.backgroundColor = "rgba(251, 191, 36, 0.3)";
  target.style.outline = "2px solid rgba(245, 158, 11, 0.8)";
  target.style.outlineOffset = "3px";
  target.style.scrollMarginTop = "72px";
  target.title = "Cited passage";
}

function fitZoom(zoom: number): number {
  return Math.min(2, Math.max(0.3, Math.round(zoom * 10) / 10));
}
