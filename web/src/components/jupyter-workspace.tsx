import { useEffect, useRef, useState } from "react";
import { ExternalLink, Notebook } from "lucide-react";
import { PageHeader } from "@/components/ui/page-header";
import { StatusMessage } from "@/components/ui/status-message";
import type { WorkspaceConfiguration } from "./workspace-url";
import { PersonalFileBrowser, PersonalFilePreview } from "./personal-file-browser";

export function JupyterWorkspace({ configuration }: { configuration: WorkspaceConfiguration }) {
  if (configuration.status !== "configured") return <section aria-label="Files and notebooks" className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-6">
    <div className="max-w-md space-y-3">
      <PageHeader title="Files" description="Documents you imported and files you created in your workspace." />
      <StatusMessage kind="signin" detail="Your workspace is not connected. Chat is still available." />
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
    <PageHeader title="Files" description="Documents you imported and files you created in your workspace." actions={<a href={configuration.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90">Open in JupyterLab<ExternalLink aria-hidden="true" className="size-4" /></a>} />
    <div inert={!!selection} className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1">
        <PersonalFileBrowser configuration={configuration} onOpenFile={openCopy} />
        <div className="flex min-h-0 min-w-0 flex-1 items-center justify-center overflow-auto p-6 sm:p-10">
          <div className="w-full max-w-lg space-y-5 rounded-xl border bg-muted/20 p-6 sm:p-8">
            <Notebook aria-hidden="true" className="size-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Open JupyterLab to edit files and run notebooks, then use @ files and / skills to work with the assistant in chat.</p>
          </div>
        </div>
      </div>
    </div>
    {selection && <PersonalFilePreview configuration={configuration} selection={selection} onClose={() => setSelection(null)} />}
  </section>;
}
