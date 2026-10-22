import { FolderOpenIcon, MenuIcon, MessageSquareIcon, NotebookPenIcon, PanelLeftCloseIcon, SparklesIcon } from "lucide-react";
import { useState, type ReactNode } from "react";

type WorkspaceSection = "files" | "skills" | "saved-skills";
const sections: { id: WorkspaceSection; label: string; icon: typeof FolderOpenIcon }[] = [
  { id: "files", label: "Files", icon: FolderOpenIcon },
  { id: "skills", label: "Skills", icon: NotebookPenIcon },
  { id: "saved-skills", label: "Saved skills", icon: SparklesIcon },
];
const navClass = "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm text-foreground/80 transition-colors hover:bg-foreground/5 hover:text-foreground";
const activeClass = "bg-foreground/[0.07] font-medium text-foreground";

interface WorkspaceShellProps {
  chat: ReactNode;
  files: ReactNode;
  skills: ReactNode;
  savedSkills?: (showChat: () => void) => ReactNode;
  /** The chat list shown in the sidebar while Chat is open. */
  chats?: ReactNode;
  /** The signed-in account and its actions, at the foot of the sidebar. */
  account?: ReactNode;
}

/** The signed-in frame: a sidebar for navigation and chats, and one main view at a time.
 * Every view stays mounted once visited, so a running chat or Jupyter session survives switching. */
export function WorkspaceShell({ chat, files, skills, savedSkills, chats, account }: WorkspaceShellProps) {
  const [page, setPage] = useState<"chat" | "workspace">("chat");
  const [active, setActive] = useState<WorkspaceSection>("files");
  const [visited, setVisited] = useState<WorkspaceSection[]>([]);
  const [open, setOpen] = useState(false);
  const showChat = () => { setPage("chat"); setOpen(false); };
  const availableSections = sections.filter(section => section.id !== "saved-skills" || savedSkills);
  const content = { files, skills, "saved-skills": savedSkills?.(showChat) };
  const visit = (id: WorkspaceSection) => setVisited(previous => previous.includes(id) ? previous : [...previous, id]);
  const openWorkspace = () => { visit(active); setPage("workspace"); };
  const select = (id: WorkspaceSection) => { setActive(id); visit(id); setPage("workspace"); setOpen(false); };

  return <div className="relative flex min-h-0 flex-1">
    <button type="button" aria-label="Open sidebar" className="absolute top-2 left-2 z-30 rounded-lg p-2 text-muted-foreground hover:bg-foreground/5 md:hidden" onClick={() => setOpen(true)}>
      <MenuIcon className="size-4" />
    </button>
    {open && <div aria-hidden className="fixed inset-0 z-30 bg-black/20 md:hidden" onClick={() => setOpen(false)} />}
    <aside aria-label="Sidebar" className={`${open ? "flex" : "hidden"} fixed inset-y-0 left-0 z-40 w-72 flex-col border-r bg-sidebar md:static md:z-auto md:flex md:w-64 md:shrink-0`}>
      <div className="flex h-12 shrink-0 items-center justify-between px-3">
        <span className="flex items-center gap-2 text-sm font-semibold tracking-tight"><SparklesIcon className="size-4 text-primary" />CRAFT</span>
        <button type="button" aria-label="Close sidebar" className="rounded-lg p-1.5 text-muted-foreground hover:bg-foreground/5 md:hidden" onClick={() => setOpen(false)}>
          <PanelLeftCloseIcon className="size-4" />
        </button>
      </div>
      <nav aria-label="Primary navigation" className="flex shrink-0 flex-col gap-0.5 px-2">
        <button type="button" aria-pressed={page === "chat"} aria-controls="craft-chat" className={`${navClass} ${page === "chat" ? activeClass : ""}`} onClick={() => { setPage("chat"); setOpen(false); }}><MessageSquareIcon className="size-4" />Chat</button>
        <button type="button" aria-pressed={page === "workspace"} aria-controls="craft-workspace" className={`${navClass} ${page === "workspace" ? activeClass : ""}`} onClick={openWorkspace}><FolderOpenIcon className="size-4" />Workspace</button>
      </nav>
      {page === "workspace" && <nav aria-label="Workspace sections" className="mt-1 ml-5 flex shrink-0 flex-col gap-0.5 border-l pr-2 pl-2">
        {availableSections.map(section => <button key={section.id} type="button" aria-pressed={active === section.id} aria-controls={`craft-workspace-${section.id}`} className={`${navClass} ${active === section.id ? activeClass : ""}`} onClick={() => select(section.id)}><section.icon className="size-4" />{section.label}</button>)}
      </nav>}
      {/* Chats stay listed on every page; choosing one returns to the chat. */}
      <div className="mt-3 flex min-h-0 flex-1 flex-col" onClickCapture={event => { if ((event.target as HTMLElement).closest("button")) setPage("chat"); }}>{chats}</div>
      {account && <div className="shrink-0 border-t p-2">{account}</div>}
    </aside>
    <section id="craft-chat" aria-label="Agent chat" hidden={page !== "chat"} className={`${page === "chat" ? "flex" : "hidden"} min-h-0 min-w-0 flex-1 flex-col`}>
      {chat}
    </section>
    <section id="craft-workspace" aria-label="Workspace" hidden={page !== "workspace"} className={`${page === "workspace" ? "flex" : "hidden"} min-h-0 min-w-0 flex-1 flex-col`}>
      {availableSections.map(section => <div key={section.id} id={`craft-workspace-${section.id}`} hidden={active !== section.id} className={`${active === section.id ? "flex" : "hidden"} min-h-0 flex-1 flex-col`}>
        {visited.includes(section.id) && content[section.id]}
      </div>)}
    </section>
  </div>;
}
