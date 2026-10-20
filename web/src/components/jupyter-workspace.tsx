import { useState } from "react";
import type { WorkspaceConfiguration } from "./workspace-url";
import { SourceImport } from "./source-import";
import { PersonalFileBrowser, PersonalFilePreview } from "./personal-file-browser";
import { personalFileFormat } from "@/lib/personal-preview";

export function JupyterWorkspace({ configuration }: { configuration: WorkspaceConfiguration }) {
  if (configuration.status !== "configured") return <section aria-label="Files and notebooks" className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-6">
    <div className="max-w-md space-y-3 rounded-lg border bg-muted/30 p-6">
      <h2 className="text-lg font-medium">Workspace service is not connected</h2>
      <p className="text-sm text-muted-foreground">Your chat stays available. Connect the local JupyterLab workspace to use personal files and Markdown skills.</p>
      {configuration.status === "invalid" && <p role="alert" className="text-sm">The workspace address must be a separate local HTTP server root without credentials, a query or a fragment.</p>}
      <p className="text-sm text-muted-foreground">JupyterLab provides files, notebooks, Python editing, terminals and downloads. USER_ROOT and agent file access require the workspace service binding.</p>
    </div>
  </section>;

  return <ConnectedWorkspace key={configuration.url} configuration={configuration} />;
}

function ConnectedWorkspace({ configuration }: { configuration: WorkspaceConfiguration & { status: "configured" } }) {
  const [selection, setSelection] = useState<{ path: string } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const openCopy = (path: string) => setSelection({ path });
  const imported = (path: string) => {
    setRefresh(value => value + 1);
    const format = personalFileFormat(path);
    if (format === "docx" || format === "xlsx") openCopy(path);
  };
  return <section aria-label="Files and notebooks" className="relative flex min-h-0 flex-1 flex-col">
    <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
      <div className="min-w-0 space-y-1">
        <h2 className="text-sm font-medium">JupyterLab</h2>
        <p className="text-xs text-muted-foreground">If the embedded view cannot load, open the workspace in a new tab and sign in there if needed.</p>
        <p className="break-words text-xs text-muted-foreground">Mention an exact personal path such as <code>/workspace/scripts/analysis.py</code> in chat so the agent can read, edit or run it; Import source originals first.</p>
      </div>
      <a href={configuration.url} target="_blank" rel="noopener noreferrer" className="shrink-0 rounded-md border px-3 py-2 text-sm hover:bg-muted">Open JupyterLab ↗</a>
    </div>
    <details className="max-h-64 shrink-0 overflow-y-auto border-b">
      <summary className="cursor-pointer px-4 py-3 text-sm">Search and import sources</summary>
      <SourceImport configuration={configuration} onImported={imported} onOpenCopy={openCopy} />
    </details>
    <div className="flex min-h-0 flex-1">
      <PersonalFileBrowser configuration={configuration} onOpenFile={openCopy} refreshToken={refresh} />
      <iframe src={configuration.url} title="JupyterLab workspace" referrerPolicy="no-referrer" className="min-h-0 min-w-0 flex-1 border-0 bg-background" />
    </div>
    {selection && <PersonalFilePreview configuration={configuration} selection={selection} onClose={() => setSelection(null)} />}
  </section>;
}
