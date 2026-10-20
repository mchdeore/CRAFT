import { useEffect, useId, useRef, useState } from "react";
import { ExternalLink, Notebook, Search, X } from "lucide-react";
import type { WorkspaceConfiguration } from "./workspace-url";
import { SourceImport } from "./source-import";
import { PersonalFileBrowser, PersonalFilePreview } from "./personal-file-browser";

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
  const [revealFile, setRevealFile] = useState<string>();
  const [searchOpen, setSearchOpen] = useState(false);
  const searchId = useId();
  const searchButton = useRef<HTMLButtonElement>(null);
  const searchPanel = useRef<HTMLDivElement>(null);
  const previewReturnFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!searchOpen) return;
    const trigger = searchButton.current;
    searchPanel.current?.querySelector<HTMLInputElement>("input")?.focus();
    return () => { trigger?.focus(); };
  }, [searchOpen]);
  useEffect(() => {
    if (selection || !previewReturnFocus.current) return;
    const target = previewReturnFocus.current;
    (target.isConnected && !target.closest("[hidden]") ? target : searchButton.current)?.focus();
    previewReturnFocus.current = null;
  }, [selection]);
  const openCopy = (path: string) => {
    previewReturnFocus.current = searchOpen ? searchButton.current : document.activeElement instanceof HTMLElement ? document.activeElement : searchButton.current;
    setSearchOpen(false);
    setSelection({ path });
  };
  const imported = (path: string) => {
    setRevealFile(path);
    setRefresh(value => value + 1);
  };
  return <section aria-label="Files and notebooks" className="relative flex min-h-0 flex-1 flex-col">
    <div ref={searchPanel} id={searchId} role="dialog" aria-modal="true" aria-label="Search and import sources" hidden={!searchOpen} className="absolute inset-3 z-40 flex min-h-0 flex-col overflow-hidden rounded-xl border bg-background shadow-xl sm:inset-6" onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setSearchOpen(false); }
      if (event.key === "Tab") {
        const controls = [...event.currentTarget.querySelectorAll<HTMLElement>("button, a[href], input, select, textarea, [tabindex]")].filter(control => !control.hasAttribute("disabled") && control.getAttribute("tabindex") !== "-1");
        const first = controls[0], last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) { event.preventDefault(); (event.shiftKey ? last : first)?.focus(); }
      }
    }}>
      <header className="flex shrink-0 items-center justify-between gap-3 border-b px-4 py-3"><h2 className="text-sm font-medium">Search and import sources</h2><button type="button" aria-label="Close source search" className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted" onClick={() => setSearchOpen(false)}><X aria-hidden="true" className="size-4" />Close</button></header>
      <div className="min-h-0 flex-1 overflow-auto">
        <SourceImport configuration={configuration} onImported={imported} onOpenCopy={openCopy} />
      </div>
    </div>
    <div inert={searchOpen || !!selection} className="flex min-h-0 flex-1 flex-col">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
        <h2 className="text-sm font-medium">Files and notebooks</h2>
        <button ref={searchButton} type="button" aria-label="Search sources" aria-haspopup="dialog" aria-expanded={searchOpen} aria-controls={searchId} className="inline-flex shrink-0 items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted" onClick={() => setSearchOpen(true)}><Search aria-hidden="true" className="size-4" />Search sources</button>
      </header>
      <div className="flex min-h-0 flex-1">
        <PersonalFileBrowser configuration={configuration} onOpenFile={openCopy} refreshToken={refresh} revealFile={revealFile} />
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
