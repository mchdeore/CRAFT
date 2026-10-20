import { useCallback, useEffect, useRef, useState } from "react";
import type { SkillCatalog, ToolInfo } from "craft-api/api/types";
import { changeSkillDraft, fetchSkill, fetchSkills, insertSkillText, insertSkillTool, newSkillDraft, openSkillDraft, prepareSkillSave, saveSkill, SkillError, type SkillDraft, type SkillFields, type SkillSelection } from "@/lib/skills";
import { fetchToolReference, filterTools, ToolReferenceError } from "@/lib/tools-reference";
import { Textarea } from "@/components/ui/textarea";

type Status = "loading" | "ready" | "error";
type Busy = "idle" | "opening" | "saving" | "trying";
interface ViewProps {
  listStatus: Status; catalog: SkillCatalog | null; editor: SkillDraft | null;
  tools?: ToolInfo[]; toolStatus?: Status; busy?: Busy; error?: SkillError | null;
  savedRevision?: number | null; tryPending?: boolean; onTry?: () => void;
  onNew?: () => void; onRefresh?: () => void; onLoadMore?: () => void; onOpen?: (id: string) => void;
  onChange?: (changes: Partial<SkillFields>) => void; onSave?: () => void; onReload?: () => void; onRetryTools?: () => void;
}
const documentTemplate = "# Goal\n\n## When to use\n\n## Steps\n1. \n\n## Tools and files\n";
const documentPlaceholder = "# Goal\nWhat should this skill accomplish?\n\n## When to use\nWhen should the agent use it?\n\n## Steps\n1. Describe the approach.\n\n## Tools and files\nAdd /tool references and exact personal paths.";
function schemaObject(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value); }

export function SkillToolCard({ tool, disabled, onInsert }: { tool: ToolInfo; disabled: boolean; onInsert: () => void }) {
  const schema = schemaObject(tool.inputSchema) ? tool.inputSchema : null;
  const parameters = schema && schemaObject(schema.properties) ? Object.entries(schema.properties) : null;
  const required = Array.isArray(schema?.required) ? schema.required : [];
  return <article className="space-y-2 rounded border bg-background p-3">
    <button type="button" aria-label={`Insert /${tool.name}`} className="break-all text-left font-mono text-sm underline-offset-4 hover:underline disabled:opacity-50" disabled={disabled} onMouseDown={event => event.preventDefault()} onClick={onInsert}>Insert /{tool.name}</button>
    <p className="whitespace-pre-wrap text-sm text-muted-foreground">{tool.description}</p>
    {!tool.availableIn.includes("chat") && <p className="text-xs text-muted-foreground">Not available in chat.</p>}
    {tool.needsApproval && <p className="text-xs text-muted-foreground">Approval required.</p>}
    {parameters?.length ? <dl className="space-y-2 text-xs">{parameters.map(([name, value]) => {
      const detail = schemaObject(value) ? value : {};
      const type = typeof detail.type === "string" ? detail.type : typeof detail.$ref === "string" ? detail.$ref : null;
      return <div key={name}><dt className="break-all"><code>{name}</code>{type && ` · ${type}`}{required.includes(name) && " · required"}</dt>{typeof detail.description === "string" && <dd className="mt-1 text-muted-foreground">{detail.description}</dd>}</div>;
    })}</dl> : <p className="text-xs text-muted-foreground">{!schema ? "Parameter metadata unavailable." : parameters ? "No parameters declared." : "See the input schema for parameter details."}</p>}
    {schema && <details><summary className="cursor-pointer text-xs">Input schema</summary><pre className="mt-2 max-h-48 overflow-auto rounded bg-muted p-2 text-xs">{JSON.stringify(schema, null, 2)}</pre></details>}
  </article>;
}

type EditorProps = Pick<ViewProps, "onChange" | "onSave" | "onReload" | "onRetryTools" | "onTry"> & { editor: SkillDraft; tools: ToolInfo[]; toolStatus: Status; busy: Busy; tryPending: boolean };
function SkillEditor({ editor, tools, toolStatus, busy, tryPending, onChange, onSave, onReload, onRetryTools, onTry }: EditorProps) {
  const [query, setQuery] = useState(""); const [insertionError, setInsertionError] = useState<SkillError | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const pendingCaret = useRef<{ instructions: string; caret: number } | null>(null);
  const occupied = busy !== "idle";
  const canTry = editor.expectedRevision > 0 && !editor.dirty && toolStatus === "ready" && editor.fields.toolNames.every(name => tools.some(tool => tool.name === name && tool.availableIn.includes("chat")));
  const filtered = filterTools(tools, query, "all");
  const references = editor.fields.toolNames.map(name => ({ name, description: tools.find(tool => tool.name === name)?.description ?? (toolStatus === "ready" ? "Unavailable" : "Saved reference; checking availability") })).sort((a, b) => a.name.localeCompare(b.name));
  useEffect(() => {
    const pending = pendingCaret.current; pendingCaret.current = null;
    if (pending && pending.instructions === editor.fields.instructions && textarea.current) { textarea.current.focus(); textarea.current.setSelectionRange(pending.caret, pending.caret); }
  }, [editor.fields]);
  function insert(name?: string) {
    if (occupied || !onChange || name && toolStatus !== "ready") return;
    const start = textarea.current?.selectionStart ?? editor.fields.instructions.length;
    const end = textarea.current?.selectionEnd ?? start;
    try {
      const addition = `${start > 0 && !editor.fields.instructions.slice(0, start).endsWith("\n") ? "\n\n" : ""}${documentTemplate}\n`;
      const result = name ? insertSkillTool(editor.fields, tools, name, start, end) : insertSkillText(editor.fields, addition, start);
      pendingCaret.current = { instructions: result.fields.instructions, caret: result.caret };
      onChange(name ? { instructions: result.fields.instructions, toolNames: result.fields.toolNames } : { instructions: result.fields.instructions });
      setInsertionError(null);
    } catch { setInsertionError(new SkillError("invalid_input")); }
  }
  return <form className="space-y-4" onSubmit={event => { event.preventDefault(); onSave?.(); }}>
    <p className="text-sm text-muted-foreground">{editor.expectedRevision ? `Revision ${editor.expectedRevision}` : "New definition"}</p>
    {insertionError && <p role="alert">{insertionError.message}</p>}
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <fieldset disabled={occupied} className="min-w-0 space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">Name<input className="mt-1 w-full rounded border bg-background p-2" value={editor.fields.name} required maxLength={400} onChange={event => onChange?.({ name: event.target.value })} /></label>
          <label className="block text-sm">Description<textarea className="mt-1 w-full rounded border bg-background p-2" value={editor.fields.description} maxLength={4000} rows={2} onChange={event => onChange?.({ description: event.target.value })} /></label>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><h2 className="font-medium">Skill document</h2><p className="text-xs text-muted-foreground">Write reusable instructions in Markdown.</p></div>
          <button type="button" className="rounded border px-3 py-1.5 text-sm" disabled={occupied || !onChange} onMouseDown={event => event.preventDefault()} onClick={() => insert()}>Insert template</button>
        </div>
        <label className="block text-sm">Instructions<Textarea ref={textarea} className="mt-1 min-h-[24rem] resize-y bg-background p-4 font-mono text-sm leading-relaxed placeholder:text-muted-foreground/50" value={editor.fields.instructions} placeholder={documentPlaceholder} required maxLength={200000} rows={18} onChange={event => { pendingCaret.current = null; setInsertionError(null); onChange?.({ instructions: event.target.value }); }} /></label>
        <details className="rounded border p-3"><summary className="cursor-pointer text-sm">Examples and tool references</summary>
          <div className="mt-3 space-y-3">
            <label className="block text-sm">Example inputs<textarea className="mt-1 w-full rounded border bg-background p-2" value={editor.fields.exampleInputs.join("\n")} rows={3} onChange={event => onChange?.({ exampleInputs: event.target.value === "" ? [] : event.target.value.split(/\r?\n/) })} /></label>
            <p className="text-xs text-muted-foreground">One example per line, up to 20 examples of 2,000 characters each.</p>
            <fieldset className="max-h-48 space-y-2 overflow-auto"><legend className="text-sm">Tool references</legend>
              {references.length === 0 && <p className="text-xs text-muted-foreground">Insert a tool from the sidebar to add its reference.</p>}
              {references.map(tool => <label key={tool.name} className="block text-sm"><input type="checkbox" checked onChange={() => onChange?.({ toolNames: editor.fields.toolNames.filter(name => name !== tool.name) })} /> <span className="font-mono">{tool.name}</span> — {tool.description}</label>)}
            </fieldset>
          </div>
        </details>
      </fieldset>
      <aside aria-label="Registered tools" className="min-w-0 space-y-3 rounded-lg border bg-muted/20 p-3">
        <h2 className="font-medium">Tools</h2>
        <label className="block text-sm">Search tools<input aria-label="Search tools" type="search" className="mt-1 w-full rounded border bg-background p-2" placeholder="Name or purpose" value={query} onInput={event => setQuery(event.currentTarget.value)} /></label>
        {toolStatus === "loading" && <p role="status" className="text-sm">Loading tool references…</p>}
        {toolStatus === "error" && <><p role="alert" className="text-sm">Could not load tool references.</p><button type="button" className="rounded border px-3 py-2 text-sm" onClick={onRetryTools} disabled={occupied}>Retry tool references</button></>}
        {toolStatus === "ready" && <p className="text-xs text-muted-foreground">{filtered.length} of {tools.length} registered tools</p>}
        {toolStatus === "ready" && filtered.length === 0 && <p className="text-sm text-muted-foreground">No matching tools.</p>}
        <div className="max-h-[34rem] space-y-3 overflow-y-auto">{filtered.map(tool => <SkillToolCard key={tool.name} tool={tool} disabled={occupied || toolStatus !== "ready" || !onChange || !tool.availableIn.includes("chat")} onInsert={() => insert(tool.name)} />)}</div>
      </aside>
    </div>
    <div className="flex flex-wrap gap-2">
      <button type="submit" className="rounded border px-3 py-2" disabled={occupied || !editor.dirty || toolStatus !== "ready"}>{busy === "saving" ? "Saving…" : "Save skill"}</button>
      <button type="button" className="rounded border px-3 py-2" onClick={onTry} disabled={occupied || tryPending || !canTry || !onTry}>{busy === "trying" ? "Starting chat…" : "Try skill"}</button>
      {editor.expectedRevision > 0 && <button type="button" className="rounded border px-3 py-2" onClick={onReload} disabled={occupied}>Reload saved version</button>}
    </div>
    {editor.expectedRevision > 0 && <p className="text-xs text-muted-foreground">Reload replaces your edits with the latest saved version.</p>}
  </form>;
}

export function SkillsView({ listStatus, catalog, editor, tools = [], toolStatus = "loading", busy = "idle", error, savedRevision, onNew, onRefresh, onLoadMore, onOpen, onChange, onSave, onReload, onRetryTools, onTry, tryPending = false }: ViewProps) {
  const occupied = busy !== "idle";
  return <section aria-label="Saved skills" className="min-h-0 flex-1 overflow-auto p-4">
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-xl font-semibold">Skills</h1><div className="flex gap-2">
        <button type="button" className="rounded border px-3 py-2" onClick={onNew} disabled={occupied}>New skill</button>
        <button type="button" className="rounded border px-3 py-2" onClick={onRefresh} disabled={occupied || listStatus === "loading"}>Refresh skills</button>
      </div></div>
      <p className="text-sm text-muted-foreground">Write a reusable skill document. Each save keeps a numbered revision. Save your edits before trying that revision in a new chat.</p>
      {listStatus === "loading" && <p role="status">Loading skills…</p>}
      {listStatus === "error" && <p role="alert">Could not load skills. Use Refresh skills to try again.</p>}
      {listStatus === "ready" && catalog?.items.length === 0 && <p className="text-sm text-muted-foreground">No saved skills.</p>}
      {!!catalog?.items.length && <details className="rounded border p-3"><summary className="cursor-pointer text-sm">Saved skills ({catalog.items.length})</summary><div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {catalog.items.map(item => <article key={item.id} className="min-w-0 space-y-1 rounded border p-3">
          <h2 className="break-words font-medium">{item.name}</h2><p className="break-words text-sm text-muted-foreground">{item.description}</p>
          <p className="text-sm">Revision {item.revision}</p><button type="button" className="rounded border px-3 py-1 text-sm" onClick={() => onOpen?.(item.id)} disabled={occupied}>Open {item.name}</button>
        </article>)}
        {catalog.nextCursor && <button type="button" className="rounded border px-3 py-2" disabled={occupied || listStatus === "loading"} onClick={onLoadMore}>Load more</button>}
      </div></details>}
      {error && <p role="alert">{error.message}</p>}
      {savedRevision && <p role="status">Saved revision {savedRevision}.</p>}
      {busy === "opening" && <p role="status">Opening saved skill…</p>}
      {editor ? <SkillEditor key={editor.id} editor={editor} tools={tools} toolStatus={toolStatus} busy={busy} tryPending={tryPending} onChange={onChange} onSave={onSave} onReload={onReload} onRetryTools={onRetryTools} onTry={onTry} /> : <p>Create a skill or open a saved definition.</p>}
    </div>
  </section>;
}
interface RequestSlot { epoch: number; controller: AbortController | null }
export function SkillsWorkspace({ onAuthInvalid, onTry, tryPending = false }: { onAuthInvalid: () => void; onTry?: (skill: SkillSelection) => Promise<boolean>; tryPending?: boolean }) {
  const [catalog, setCatalog] = useState<SkillCatalog | null>(null);
  const [listStatus, setListStatus] = useState<Status>("loading");
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [toolStatus, setToolStatus] = useState<Status>("loading");
  const [editor, setEditor] = useState<SkillDraft | null>(null);
  const [busy, setBusy] = useState<Busy>("idle");
  const [error, setError] = useState<SkillError | null>(null);
  const [savedRevision, setSavedRevision] = useState<number | null>(null);
  const mounted = useRef(false); const editorBusy = useRef(false);
  const catalogRequest = useRef<RequestSlot>({ epoch: 0, controller: null });
  const toolRequest = useRef<RequestSlot>({ epoch: 0, controller: null });
  const editorRequest = useRef<RequestSlot>({ epoch: 0, controller: null });
  function begin(slot: RequestSlot) { slot.controller?.abort(); slot.epoch++; slot.controller = new AbortController(); return { epoch: slot.epoch, signal: slot.controller.signal }; }
  function current(slot: RequestSlot, request: { epoch: number; signal: AbortSignal }) { return mounted.current && slot.epoch === request.epoch && !request.signal.aborted; }
  const refresh = useCallback(async (after?: string) => {
    const active = begin(catalogRequest.current);
    try {
      const result = await fetchSkills(active.signal, after);
      if (!current(catalogRequest.current, active)) return;
      setCatalog(previous => after && previous?.nextCursor === after ? { items: [...previous.items, ...result.items.filter(item => !previous.items.some(old => old.id === item.id))], nextCursor: result.nextCursor } : result);
      setListStatus("ready");
    } catch (failure) {
      if (!current(catalogRequest.current, active)) return;
      if (failure instanceof SkillError && failure.code === "unauthenticated") onAuthInvalid(); else setListStatus("error");
    }
  }, [onAuthInvalid]);
  const refreshTools = useCallback(async () => {
    const active = begin(toolRequest.current);
    try { const result = await fetchToolReference(active.signal); if (current(toolRequest.current, active)) { setTools(result); setToolStatus("ready"); } }
    catch (failure) { if (!current(toolRequest.current, active)) return; if (failure instanceof ToolReferenceError && failure.code === "unauthenticated") onAuthInvalid(); else setToolStatus("error"); }
  }, [onAuthInvalid]);
  useEffect(() => {
    mounted.current = true; const slots = [catalogRequest.current, toolRequest.current, editorRequest.current]; void refresh(); void refreshTools();
    return () => { mounted.current = false; slots.forEach(slot => slot.controller?.abort()); };
  }, [refresh, refreshTools]);
  async function open(id: string) {
    if (editorBusy.current) return; editorBusy.current = true;
    const active = begin(editorRequest.current); setBusy("opening"); setError(null); setSavedRevision(null);
    try { const value = await fetchSkill(id, active.signal); if (current(editorRequest.current, active)) setEditor(openSkillDraft(value)); }
    catch (failure) { if (!current(editorRequest.current, active)) return; if (failure instanceof SkillError && failure.code === "unauthenticated") onAuthInvalid(); else setError(failure instanceof SkillError ? failure : new SkillError("unavailable")); }
    finally { if (current(editorRequest.current, active)) { editorBusy.current = false; setBusy("idle"); } }
  }
  async function trySkill() {
    if (!editor || editor.dirty || editor.expectedRevision < 1 || editorBusy.current || tryPending || toolStatus !== "ready" || !onTry) return;
    editorBusy.current = true;
    const active = begin(editorRequest.current); setBusy("trying"); setError(null);
    try {
      const value = await fetchSkill(editor.id, active.signal, editor.expectedRevision);
      if (!current(editorRequest.current, active)) return;
      if (value.unavailableTools.length || !value.toolNames.every(name => tools.some(tool => tool.name === name && tool.availableIn.includes("chat")))) throw new SkillError("invalid_input");
      if (!await onTry({ id: value.id, revision: value.revision, name: value.name }) && current(editorRequest.current, active)) throw new SkillError("unavailable");
    } catch (failure) {
      if (!current(editorRequest.current, active)) return;
      if (failure instanceof SkillError && failure.code === "unauthenticated") onAuthInvalid(); else setError(failure instanceof SkillError ? failure : new SkillError("unavailable"));
    } finally { if (current(editorRequest.current, active)) { editorBusy.current = false; setBusy("idle"); } }
  }
  async function save() {
    if (!editor || editorBusy.current || !editor.dirty) return; editorBusy.current = true;
    const attempt = prepareSkillSave(editor); setEditor(attempt.draft);
    const active = begin(editorRequest.current); setBusy("saving"); setError(null); setSavedRevision(null);
    let acknowledged = false;
    try { const value = await saveSkill(attempt.request, active.signal); if (current(editorRequest.current, active)) { setEditor(openSkillDraft(value)); setSavedRevision(value.revision); acknowledged = true; } }
    catch (failure) { if (!current(editorRequest.current, active)) return; if (failure instanceof SkillError && failure.code === "unauthenticated") onAuthInvalid(); else setError(failure instanceof SkillError ? failure : new SkillError("unavailable")); }
    finally { if (current(editorRequest.current, active)) { editorBusy.current = false; setBusy("idle"); } }
    if (acknowledged && current(editorRequest.current, active)) { setListStatus("loading"); void refresh(); }
  }
  function fresh() { if (editorBusy.current) return; editorRequest.current.controller?.abort(); editorRequest.current.epoch++; setEditor(newSkillDraft()); setError(null); setSavedRevision(null); }
  return <SkillsView listStatus={listStatus} catalog={catalog} editor={editor} tools={tools} toolStatus={toolStatus} busy={busy} error={error} savedRevision={savedRevision} tryPending={tryPending} onTry={() => void trySkill()}
    onNew={fresh} onRefresh={() => { setListStatus("loading"); void refresh(); }} onLoadMore={() => { if (catalog?.nextCursor) { setListStatus("loading"); void refresh(catalog.nextCursor); } }} onOpen={id => void open(id)}
    onChange={changes => { if (editorBusy.current) return; setEditor(previous => previous ? changeSkillDraft(previous, changes) : null); setError(null); setSavedRevision(null); }}
    onSave={() => void save()} onReload={() => { if (editor) void open(editor.id); }} onRetryTools={() => { setToolStatus("loading"); void refreshTools(); }} />;
}
