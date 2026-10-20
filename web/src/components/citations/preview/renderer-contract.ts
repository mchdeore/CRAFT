import type { PreviewDocument } from "@/components/citations/citation-context";

export type DocumentFormat = "docx" | "xlsx" | "pdf" | "text" | "png";

// Every format renderer receives the same inputs. Format-specific details,
// such as a cited page, heading, sheet, or row, come from the source metadata.
export interface DocumentRendererProps {
  blob: Blob;
  source: PreviewDocument;
}
