import { useEffect, useRef, useState } from "react";
import type { ToolInfo } from "craft-api/api/types";
import type { WorkspaceConfiguration } from "@/components/workspace-url";
import { SkillToolCard } from "@/components/skills-workspace";
import { Textarea } from "@/components/ui/textarea";
import { fetchToolReference, filterTools, ToolReferenceError } from "@/lib/tools-reference";
import { listPersonalFiles, listPersonalSkills, loadPersonalSkill, MAX_SKILL_BYTES, PersonalWorkspaceError, personalAbsolutePath, savePersonalSkill, type PersonalEntry } from "@/lib/personal-workspace";
import { insertPersonalDocumentText, personalPathText } from "@/lib/personal-references";

type Status = "idle" | "loading" | "ready" | "error";
interface SkillDocument { filename: string; content: string; path?: string }
const template = "# Goal\nDescribe the result this skill should produce.\n\n## When to use\nExplain when these instructions apply.\n\n## Steps\n1. Read the relevant inputs.\n2. Perform the analysis.\n3. Save and check the result.\n\n## Tools and files\nList tool references and exact personal paths.\n";
const placeholder = "# Goal\nWhat should this skill achieve?\n\n## When to use\nWhen should the agent use it?\n\n## Steps\n1. ...\n\n## Tools and files\nUse the sidebar to insert references.";
const buttonClass = "rounded-md border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50";

export function PersonalSkillsEditor({ document, tools, toolStatus, busy, canSave = true, files = [], fileStatus = "idle", onChange, onSave, onRetryTools, onBrowseFiles }: {
  document: SkillDocument; tools: ToolInfo[]; toolStatus: Status; busy: boolean; canSave?: boolean;
  files?: PersonalEntry[]; fileStatus?: Status; onChange: (patch: Partial<SkillDocument>) => void;
  onSave: () => void; onRetryTools?: () => void; onBrowseFiles?: () => void;
}) {
  const [query, setQuery] = useState(""); const [fileQuery, setFileQuery] = useState("");
  const [insertionError, setInsertionError] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const pendingCaret = useRef<number | null>(null);
  useEffect(() => {
    if (pendingCaret.current === null || !textarea.current) return;
    textarea.current.focus(); textarea.current.setSelectionRange(pendingCaret.current, pendingCaret.current); pendingCaret.current = null;
  }, [document]);
  const insert = (addition: string, replace = true) => {
    if (busy) return;
    const start = textarea.current?.selectionStart ?? document.content.length;
    const end = replace ? textarea.current?.selectionEnd ?? start : start;
    try {
      const next = insertPersonalDocumentText(document.content, addition, start, end);
      pendingCaret.current = next.caret; onChange({ content: next.content }); setInsertionError(false);
    } catch { setInsertionError(true); }
  };
  const filteredFiles = files.filter(file => file.path.toLocaleLowerCase().includes(fileQuery.trim().toLocaleLowerCase())).slice(0, 20);
  return <form className="space-y-4" onSubmit={event => { event.preventDefault(); if (!busy && canSave) onSave(); }}>
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_20rem]">
      <fieldset disabled={busy} className="min-w-0 space-y-4">
        <label className="block text-sm">Skill filename<input aria-label="Skill filename" className="mt-1 w-full rounded border bg-background p-2 font-mono" value={document.filename} readOnly={!!document.path} maxLength={255} required placeholder="analysis.md" onInput={event => onChange({ filename: event.currentTarget.value })} /></label>
        <p className="break-words text-xs text-muted-foreground">{document.path ? personalAbsolutePath(document.path) : "/workspace/skills/"}{!document.path && " — use a .md filename"}</p>
        <div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="font-medium">Skill document</h2><p className="text-xs text-muted-foreground">Write the instructions the agent should follow in Markdown.</p></div><button type="button" className={buttonClass} onMouseDown={event => event.preventDefault()} onClick={() => insert(`\n${template}\n`, false)}>Insert template</button></div>
        <label className="block text-sm">Instructions<Textarea ref={textarea} aria-label="Skill document" value={document.content} placeholder={placeholder} rows={20} maxLength={MAX_SKILL_BYTES} required className="mt-1 min-h-[24rem] resize-y bg-background p-4 font-mono text-sm leading-relaxed placeholder:text-muted-foreground/50" onInput={event => { pendingCaret.current = null; onChange({ content: event.currentTarget.value }); }} /></label>
        {insertionError && <p role="alert" className="text-sm">This reference exceeds the skill document limit.</p>}
        <button type="submit" className={buttonClass} disabled={busy || !canSave || !document.content.trim()}>{busy ? "Saving or loading…" : "Save to workspace"}</button>
        <p className="text-xs text-muted-foreground">Save writes this personal file. In chat, type / to choose a saved Markdown skill; the agent reads its current contents.</p>
      </fieldset>
      <aside aria-label="Skill context and tools" className="space-y-4 rounded-lg border bg-muted/20 p-3">
        <div className="space-y-2"><h2 className="font-medium">Files</h2><p className="text-xs text-muted-foreground">Use personal copies as inputs. Import source originals before editing them.</p><button type="button" className={buttonClass} disabled={busy || fileStatus === "loading" || !onBrowseFiles} onMouseDown={event => event.preventDefault()} onClick={onBrowseFiles}>{fileStatus === "loading" ? "Loading files…" : "Browse personal files"}</button>
          {fileStatus === "error" && <p className="text-sm">Could not load personal files. Open JupyterLab to check your sign-in.</p>}
          {fileStatus === "ready" && <><input aria-label="Search personal file references" type="search" className="w-full rounded border bg-background p-2 text-sm" value={fileQuery} onInput={event => setFileQuery(event.currentTarget.value)} placeholder="Filter paths" /><div className="max-h-48 space-y-1 overflow-auto">{filteredFiles.map(file => <button key={file.path} type="button" className="block w-full break-words rounded p-1 text-left font-mono text-xs hover:bg-muted" disabled={busy} onMouseDown={event => event.preventDefault()} onClick={() => insert(personalPathText(file.path))}>{personalAbsolutePath(file.path)}</button>)}{filteredFiles.length === 0 && <p className="text-xs">No matching personal files.</p>}</div></>}
        </div>
        <div className="space-y-2"><h2 className="font-medium">Tools</h2><input aria-label="Search tools" type="search" className="w-full rounded border bg-background p-2 text-sm" placeholder="Name or purpose" value={query} onInput={event => setQuery(event.currentTarget.value)} />
          {toolStatus === "loading" && <p role="status" className="text-sm">Loading tool references…</p>}
          {toolStatus === "error" && <><p className="text-sm">Tool references are unavailable. You can still save your document.</p><button type="button" className={buttonClass} onClick={onRetryTools}>Retry tool references</button></>}
          {toolStatus === "ready" && <p className="text-xs text-muted-foreground">{filterTools(tools, query, "all").length} of {tools.length} registered tools</p>}
          <div className="max-h-[32rem] space-y-3 overflow-auto">{filterTools(tools, query, "all").map(tool => <SkillToolCard key={tool.name} tool={tool} disabled={busy || toolStatus !== "ready" || !tool.availableIn.includes("chat")} onInsert={() => { if (tools.some(registered => registered.name === tool.name && registered.availableIn.includes("chat"))) insert(`/${tool.name}`); }} />)}</div>
        </div>
      </aside>
    </div>
  </form>;
}

export function PersonalSkillsWorkspace({ configuration, onAuthInvalid }: { configuration: WorkspaceConfiguration; onAuthInvalid: () => void }) {
  if (configuration.status !== "configured") return <div className="p-6 text-sm">Connect your personal JupyterLab workspace to load and save Markdown skills.</div>;
  return <ConnectedSkills key={configuration.url} configuration={configuration} onAuthInvalid={onAuthInvalid} />;
}
function ConnectedSkills({ configuration, onAuthInvalid }: { configuration: WorkspaceConfiguration & { status: "configured" }; onAuthInvalid: () => void }) {
  const [document, setDocument] = useState<SkillDocument>({ filename: "new-skill.md", content: "" });
  const [savedContent, setSavedContent] = useState("");
  const [skills, setSkills] = useState<PersonalEntry[]>([]), [files, setFiles] = useState<PersonalEntry[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [listStatus, setListStatus] = useState<Status>("loading"), [fileStatus, setFileStatus] = useState<Status>("idle"), [toolStatus, setToolStatus] = useState<Status>("loading");
  const [refresh, setRefresh] = useState(0), [toolRefresh, setToolRefresh] = useState(0);
  const [busy, setBusy] = useState(false), [blockedSave, setBlockedSave] = useState(false);
  const [error, setError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null);
  const [pendingSwitch, setPendingSwitch] = useState<{ path?: string } | null>(null);
  const request = useRef<AbortController | null>(null), fileRequest = useRef<AbortController | null>(null), occupied = useRef(false);
  const dirty = document.content !== savedContent;
  useEffect(() => {
    const controller = new AbortController();
    void listPersonalSkills(configuration, controller.signal).then(rows => { if (!controller.signal.aborted) { setSkills(rows); setListStatus("ready"); } }).catch(() => { if (!controller.signal.aborted) setListStatus("error"); });
    return () => controller.abort();
  }, [configuration, refresh]);
  useEffect(() => {
    const controller = new AbortController();
    void fetchToolReference(controller.signal).then(rows => { if (!controller.signal.aborted) { setTools(rows); setToolStatus("ready"); } }).catch(error => {
      if (controller.signal.aborted) return;
      if (error instanceof ToolReferenceError && error.code === "unauthenticated") onAuthInvalid(); else setToolStatus("error");
    });
    return () => controller.abort();
  }, [onAuthInvalid, toolRefresh]);
  useEffect(() => () => { request.current?.abort(); fileRequest.current?.abort(); }, []);
  const refreshList = () => { setListStatus("loading"); setRefresh(previous => previous + 1); };
  const load = async (path: string) => {
    if (occupied.current) return;
    occupied.current = true; setBusy(true); setError(null); setNotice(null);
    const controller = new AbortController(); request.current?.abort(); request.current = controller;
    try {
      const content = await loadPersonalSkill(configuration, path, controller.signal);
      if (!controller.signal.aborted) { setDocument({ filename: path.split("/").at(-1)!, content, path }); setSavedContent(content); setBlockedSave(false); }
    } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof PersonalWorkspaceError ? reason.message : "Could not load this skill document."); }
    finally { if (!controller.signal.aborted) { occupied.current = false; setBusy(false); } }
  };
  const reset = () => { setDocument({ filename: "new-skill.md", content: "" }); setSavedContent(""); setBlockedSave(false); setError(null); setNotice(null); };
  const choose = (path?: string) => {
    if (occupied.current) return;
    if (dirty) { setPendingSwitch({ path }); return; }
    if (path) void load(path); else reset();
  };
  const save = async () => {
    if (occupied.current || blockedSave || !dirty) return;
    occupied.current = true; setBusy(true); setError(null); setNotice(null);
    const controller = new AbortController(); request.current?.abort(); request.current = controller;
    const snapshot = { ...document };
    try {
      const path = await savePersonalSkill(configuration, snapshot, controller.signal);
      if (!controller.signal.aborted) { setDocument({ ...snapshot, path }); setSavedContent(snapshot.content); setNotice(`Saved ${personalAbsolutePath(path)}.`); refreshList(); }
    } catch (reason) {
      if (!controller.signal.aborted) { setError(reason instanceof PersonalWorkspaceError ? reason.message : "Save could not be confirmed. Inspect the workspace before saving again."); setBlockedSave(!(reason instanceof PersonalWorkspaceError) || reason.code === "save_unconfirmed"); }
    } finally { if (!controller.signal.aborted) { occupied.current = false; setBusy(false); } }
  };
  const browseFiles = async () => {
    fileRequest.current?.abort(); const controller = new AbortController(); fileRequest.current = controller; setFileStatus("loading");
    try { const result = await listPersonalFiles(configuration, controller.signal); if (!controller.signal.aborted) { setFiles(result.files); setFileStatus("ready"); } }
    catch { if (!controller.signal.aborted) setFileStatus("error"); }
  };
  return <section aria-label="Personal skills" className="min-h-0 flex-1 overflow-auto p-4"><div className="mx-auto max-w-6xl space-y-4">
    <header className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-xl font-semibold">Skills</h1><p className="text-sm text-muted-foreground">Markdown documents in your personal workspace.</p></div><div className="flex gap-2"><button type="button" className={buttonClass} disabled={busy} onClick={() => choose()}>New</button><a href={configuration.url} target="_blank" rel="noopener noreferrer" className={buttonClass}>Open JupyterLab ↗</a></div></header>
    <details className="rounded-lg border p-3" open><summary className="cursor-pointer text-sm">Load a workspace skill</summary><div className="mt-3 flex flex-wrap items-center gap-2">
      {listStatus === "loading" && <p role="status" className="text-sm">Loading personal skills…</p>}{listStatus === "error" && <p className="text-sm">Could not read /workspace/skills. Check your JupyterLab sign-in.</p>}{listStatus === "ready" && skills.length === 0 && <p className="text-sm text-muted-foreground">No Markdown skills yet. Save your first document below.</p>}
      {skills.map(skill => <button key={skill.path} type="button" className={buttonClass} disabled={busy} onClick={() => choose(skill.path)}>Load {skill.name}</button>)}<button type="button" className={buttonClass} disabled={busy || listStatus === "loading"} onClick={refreshList}>Refresh skills</button>
    </div></details>
    {pendingSwitch && <div role="alert" className="flex flex-wrap items-center gap-3 rounded border p-3 text-sm"><p>This replaces your unsaved instructions.</p><button type="button" className={buttonClass} onClick={() => { const next = pendingSwitch; setPendingSwitch(null); if (next.path) void load(next.path); else reset(); }}>Discard edits and continue</button><button type="button" className={buttonClass} onClick={() => setPendingSwitch(null)}>Keep editing</button></div>}
    {error && <p role="alert" className="text-sm">{error} <a className="underline" href={configuration.url} target="_blank" rel="noopener noreferrer">Open JupyterLab</a>{blockedSave && " Load the file or choose New before another save."}</p>}{notice && <p role="status" className="text-sm">{notice}</p>}
    <PersonalSkillsEditor document={document} tools={tools} toolStatus={toolStatus} files={files} fileStatus={fileStatus} busy={busy} canSave={dirty && !blockedSave && !pendingSwitch} onChange={patch => { setDocument(previous => ({ ...previous, ...patch })); setNotice(null); }} onSave={() => void save()} onRetryTools={() => { setToolStatus("loading"); setToolRefresh(previous => previous + 1); }} onBrowseFiles={() => void browseFiles()} />
  </div></section>;
}
