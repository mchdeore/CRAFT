import { useState, type ReactNode } from "react";

type WorkspaceSection = "files" | "skills";
const sections: { id: WorkspaceSection; label: string }[] = [
  { id: "files", label: "Files" },
  { id: "skills", label: "Skills" },
];
const buttonClass = "rounded-md px-4 py-2 text-sm hover:bg-muted";

export function WorkspaceShell({ chat, files, skills }: { chat: ReactNode; files: ReactNode; skills: ReactNode }) {
  const [page, setPage] = useState<"chat" | "workspace">("chat");
  const [active, setActive] = useState<WorkspaceSection>("files");
  const [visited, setVisited] = useState<WorkspaceSection[]>([]);
  const content = { files, skills };
  const visit = (id: WorkspaceSection) => setVisited(previous => previous.includes(id) ? previous : [...previous, id]);
  const openWorkspace = () => { visit(active); setPage("workspace"); };
  const select = (id: WorkspaceSection) => { setActive(id); visit(id); };

  return <div className="flex min-h-0 flex-1 flex-col">
    <nav aria-label="Primary navigation" className="flex shrink-0 gap-2 border-b bg-muted/20 px-3 py-2">
      <button type="button" aria-pressed={page === "chat"} aria-controls="craft-chat" className={`${buttonClass} ${page === "chat" ? "bg-muted font-medium" : ""}`} onClick={() => setPage("chat")}>Chat</button>
      <button type="button" aria-pressed={page === "workspace"} aria-controls="craft-workspace" className={`${buttonClass} ${page === "workspace" ? "bg-muted font-medium" : ""}`} onClick={openWorkspace}>Workspace</button>
    </nav>
    <section id="craft-chat" aria-label="Agent chat" hidden={page !== "chat"} className={`${page === "chat" ? "flex" : "hidden"} min-h-0 min-w-0 flex-1 flex-col`}>
      {chat}
    </section>
    <section id="craft-workspace" aria-label="Workspace" hidden={page !== "workspace"} className={`${page === "workspace" ? "flex" : "hidden"} min-h-0 min-w-0 flex-1 flex-col`}>
      <nav aria-label="Workspace sections" className="flex shrink-0 gap-2 border-b px-3 py-2">
        {sections.map(section => <button key={section.id} type="button" aria-pressed={active === section.id} aria-controls={`craft-workspace-${section.id}`} className={`${buttonClass} ${active === section.id ? "bg-muted font-medium" : ""}`} onClick={() => select(section.id)}>{section.label}</button>)}
      </nav>
        {sections.map(section => <div key={section.id} id={`craft-workspace-${section.id}`} hidden={active !== section.id} className={`${active === section.id ? "flex" : "hidden"} min-h-0 flex-1 flex-col`}>
          {visited.includes(section.id) && content[section.id]}
        </div>)}
    </section>
  </div>;
}
