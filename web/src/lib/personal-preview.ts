import type { DocumentFormat } from "@/components/citations/preview/renderer-contract";
import type { WorkspaceConfiguration } from "@/components/workspace-url";
import { personalAbsolutePath, personalWorkspaceOrigin } from "@/lib/personal-workspace";

export function personalFileFormat(filename: string): DocumentFormat | undefined {
  const extension = filename.split(".").at(-1)?.toLowerCase();
  if (extension === "docx" || extension === "xlsx" || extension === "pdf" || extension === "png") return extension;
  if (extension && ["txt", "md", "csv", "py", "json", "log"].includes(extension)) return "text";
  return undefined;
}
export function personalJupyterURL(configuration: WorkspaceConfiguration & { status: "configured" }, path: string): string {
  personalWorkspaceOrigin(configuration); personalAbsolutePath(path);
  return `${configuration.url}/tree/${path.split("/").map(encodeURIComponent).join("/")}`;
}
