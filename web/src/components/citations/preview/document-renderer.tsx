import { DocxRenderer } from "@/components/citations/preview/docx/docx-renderer";
import { PdfRenderer } from "@/components/citations/preview/pdf/pdf-renderer";
import type {
  DocumentFormat,
  DocumentRendererProps,
} from "@/components/citations/preview/renderer-contract";
import { XlsxRenderer } from "@/components/citations/preview/xlsx/xlsx-renderer";
import { TextRenderer } from "@/components/citations/preview/text/text-renderer";
import { PngRenderer } from "@/components/citations/preview/png-renderer";

interface DocumentRendererSelectorProps extends DocumentRendererProps {
  format: DocumentFormat;
}

// This is the only place that selects a format-specific renderer. Each
// renderer behind it implements the same DocumentRendererProps interface.
export function DocumentRenderer({
  format,
  ...props
}: DocumentRendererSelectorProps) {
  switch (format) {
    case "docx":
      return <DocxRenderer {...props} />;
    case "xlsx":
      return <XlsxRenderer {...props} />;
    case "pdf":
      return <PdfRenderer {...props} />;
    case "text":
      return <TextRenderer {...props} />;
    case "png":
      return <PngRenderer {...props} />;
  }
}
