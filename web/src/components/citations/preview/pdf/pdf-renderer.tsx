import type { DocumentRendererProps } from "@/components/citations/preview/renderer-contract";
import { useEffect, useRef } from "react";

export function PdfRenderer({ blob, source }: DocumentRendererProps) {
  const objectRef = useRef<HTMLObjectElement>(null);

  useEffect(() => {
    const objectURL = URL.createObjectURL(blob);
    const page = citedPdfPage(source.locator);
    objectRef.current?.setAttribute(
      "data",
      page ? `${objectURL}#page=${page}` : objectURL,
    );
    return () => URL.revokeObjectURL(objectURL);
  }, [blob, source.locator]);

  return (
    <object
      ref={objectRef}
      type="application/pdf"
      className="h-full min-h-[32rem] w-full"
      aria-label="PDF source preview"
    >
      <p className="text-muted-foreground text-sm">
        PDF preview is unavailable.
      </p>
    </object>
  );
}

function citedPdfPage(locator: string | undefined): number | undefined {
  if (!locator) return undefined;
  const match = /^p-(\d+)$/.exec(locator);
  if (!match) return undefined;
  const page = Number.parseInt(match[1]!, 10);
  return Number.isInteger(page) && page > 0 ? page : undefined;
}
