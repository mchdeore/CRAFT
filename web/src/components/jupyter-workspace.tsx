import { useEffect, useRef, useState } from "react";
import { ExternalLink, Notebook } from "lucide-react";
import { PageHeader } from "@/components/ui/page-header";
import { StatusMessage } from "@/components/ui/status-message";
import type { WorkspaceConfiguration } from "./workspace-url";
import { PersonalFileBrowser, PersonalFilePreview } from "./personal-file-browser";

export function JupyterWorkspace({ configuration }: { configuration: WorkspaceConfiguration }) {
  if (configuration.status !== "configured") return <section aria-label="Files and notebooks" className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-6">
    <div className="max-w-md space-y-3 rounded-lg border bg-muted/30 p-6">
      <StatusMessage kind="signin" detail="Sign in to your workspace to attach files and skills." />
      <p className="text-sm text-muted-foreground">Your chat stays available. Connect the local JupyterLab workspace to use personal files and Markdown skills.</p>
      {configuration.status === "invalid" && <p role="alert" className="text-sm">The workspace address must be a separate local HTTP server root without credentials, a query or a fragment.</p>}
      <p className="text-sm text-muted-foreground">JupyterLab provides files, notebooks, Python editing, terminals and downloads. USER_ROOT and agent file access require the workspace service binding.</p>
    </div>
  </section>;

  return <ConnectedWorkspace key={configuration.url} configuration={configuration} />;
}

function ConnectedWorkspace({ configuration }: { configuration: WorkspaceConfiguration & { status: "configured" } }) {
  const [selection, setSelection] = useState<{ path: string } | null>(null);
  const previewReturnFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (selection || !previewReturnFocus.current) return;
    const target = previewReturnFocus.current;
    if (target.isConnected && !target.closest("[hidden]")) target.focus();
    previewReturnFocus.current = null;
  }, [selection]);
  const openCopy = (path: string) => {
    previewReturnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setSelection({ path });
  };
  return <section aria-label="Files and notebooks" className="relative flex min-h-0 flex-1 flex-col">
    <PageHeader title="Workspace" />
    <div inert={!!selection} className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1">
        <PersonalFileBrowser configuration={configuration} onOpenFile={openCopy} />
        <div className="flex min-h-0 min-w-0 flex-1 items-center justify-center overflow-auto p-6 sm:p-10">
          <div className="w-full max-w-lg space-y-5 rounded-xl border bg-muted/20 p-6 sm:p-8">
            <Notebook aria-hidden="true" className="size-8 text-muted-foreground" />
            <div className="space-y-2"><h3 className="text-xl font-medium">Work in JupyterLab</h3><p className="text-sm text-muted-foreground">Edit files, run notebooks and use terminals in a separate tab. Sign in there if needed.</p></div>
            <a href={configuration.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-3 text-sm font-medium text-primary-foreground hover:bg-primary/90">Open JupyterLab<ExternalLink aria-hidden="true" className="size-4" /></a>
            <p className="text-sm text-muted-foreground">Your files and running kernels stay in JupyterLab while you browse here.</p>
            <p className="break-words text-xs text-muted-foreground">Mention a personal path such as <code>/workspace/scripts/analysis.py</code> in chat for the agent to read, edit or run. Import source originals to make personal copies first.</p>
          </div>
        </div>
      </div>
    </div>
    {selection && <PersonalFilePreview configuration={configuration} selection={selection} onClose={() => setSelection(null)} />}
  </section>;
}
