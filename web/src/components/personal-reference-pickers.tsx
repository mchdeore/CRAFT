import { ComposerPrimitive, useAui, useAuiState, unstable_useTriggerPopoverScopeContext, unstable_useTriggerPopoverTriggers } from "@assistant-ui/react";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type RefObject } from "react";
import { StatusMessage } from "@/components/ui/status-message";
import { flushSync } from "react-dom";
import type { WorkspaceConfiguration } from "@/components/workspace-url";
import { listPersonalFiles, listPersonalSkills, PersonalWorkspaceError, type PersonalEntry } from "@/lib/personal-workspace";
import { insertPersonalReferenceTrigger, personalPathFormatter, personalReferenceAdapter, personalReferenceMatcher, type PersonalReferenceKind } from "@/lib/personal-references";

type Status = "idle" | "loading" | "ready" | "error" | "signin";
export function PersonalReferencePickers({ configuration }: { configuration: WorkspaceConfiguration }) {
  const key = configuration.status === "configured" ? configuration.url : configuration.status;
  return <>
    <PersonalReferencePicker key={`${key}:skills`} kind="skills" configuration={configuration} />
    <PersonalReferencePicker key={`${key}:files`} kind="files" configuration={configuration} />
  </>;
}
export function PersonalReferenceControl({ configuration, inputRef }: { configuration: WorkspaceConfiguration; inputRef: RefObject<HTMLTextAreaElement | null> }) {
  const aui = useAui();
  const triggers = unstable_useTriggerPopoverTriggers();
  const disabled = useAuiState(state => state.thread.isDisabled);
  const [open, setOpen] = useState(false);
  const id = useId();
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !container.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close); document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  const choose = (kind: PersonalReferenceKind) => {
    const text = aui.composer.getState().text, input = inputRef.current;
    const start = input?.value === text ? input.selectionStart : text.length;
    const end = input?.value === text ? input.selectionEnd : text.length;
    const next = insertPersonalReferenceTrigger(text, kind, start, end);
    // Commit the controlled value before restoring the native textarea caret.
    flushSync(() => { aui.composer.setText(next.content); setOpen(false); });
    for (const trigger of triggers.values()) trigger.resource.setCursorPosition(next.caret);
    input?.focus({ preventScroll: true });
    input?.setSelectionRange(next.caret, next.caret);
  };
  return <div ref={container} className="relative" onKeyDown={event => { if (event.key === "Escape" && open) { event.preventDefault(); setOpen(false); inputRef.current?.focus({ preventScroll: true }); } }}>
    <button type="button" disabled={disabled} aria-expanded={open} aria-controls={id} title={configuration.status === "configured" ? "Add a personal file or Markdown skill" : "Connect your personal workspace to add references"} className="rounded px-2 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50" onMouseDown={event => event.preventDefault()} onClick={() => setOpen(value => !value)}>Add references</button>
    {open && <div id={id} role="group" aria-label="Add personal references" className="absolute bottom-full left-0 z-40 mb-2 w-56 rounded-xl border bg-popover p-2 text-popover-foreground shadow-lg">
      <button type="button" aria-label="Add workspace file" className="block w-full rounded-lg p-2 text-left text-sm hover:bg-muted" onMouseDown={event => event.preventDefault()} onClick={() => choose("files")}>Files <span className="float-right font-mono text-xs">@</span></button>
      <button type="button" aria-label="Add Markdown skill" className="block w-full rounded-lg p-2 text-left text-sm hover:bg-muted" onMouseDown={event => event.preventDefault()} onClick={() => choose("skills")}>Markdown skills <span className="float-right font-mono text-xs">/</span></button>
    </div>}
  </div>;
}
function PersonalReferencePicker({ kind, configuration }: { kind: PersonalReferenceKind; configuration: WorkspaceConfiguration }) {
  const [files, setFiles] = useState<PersonalEntry[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null), [limited, setLimited] = useState(false);
  const pending = useCallback(() => { setStatus("loading"); setError(null); setFiles([]); setLimited(false); }, []);
  const complete = useCallback((rows: PersonalEntry[], limited: boolean) => { setFiles(rows); setLimited(limited); setStatus("ready"); }, []);
  const failed = useCallback((error: string, signIn = false) => { setStatus(signIn ? "signin" : "error"); setError(error); }, []);
  const adapter = useMemo(() => personalReferenceAdapter(status === "ready" ? files : [], kind), [files, status, kind]);
  return <ComposerPrimitive.TriggerPopover char={kind === "skills" ? "/" : "@"} matcher={personalReferenceMatcher} adapter={adapter} isLoading={status === "loading"} aria-label={kind === "skills" ? "Personal Markdown skills" : "Personal workspace files"} className="absolute bottom-full left-0 z-40 mb-2 max-h-80 w-full overflow-auto rounded-xl border bg-popover p-3 text-popover-foreground shadow-lg">
    <ComposerPrimitive.TriggerPopover.Directive formatter={personalPathFormatter} />
    <PickerContents kind={kind} configuration={configuration} status={status} error={error} limited={limited} onPending={pending} onComplete={complete} onFailed={failed} />
  </ComposerPrimitive.TriggerPopover>;
}
function PickerContents({ kind, configuration, status, error, limited, onPending, onComplete, onFailed }: {
  kind: PersonalReferenceKind; configuration: WorkspaceConfiguration; status: Status; error: string | null; limited: boolean;
  onPending: () => void; onComplete: (files: PersonalEntry[], limited: boolean) => void; onFailed: (message: string, signIn?: boolean) => void;
}) {
  const { open, close, popoverId } = unstable_useTriggerPopoverScopeContext();
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: MouseEvent) => {
      const target = event.target;
      if (target instanceof Node && !document.getElementById(popoverId)?.contains(target) && !(target instanceof HTMLTextAreaElement)) close();
    };
    document.addEventListener("mousedown", dismiss);
    return () => document.removeEventListener("mousedown", dismiss);
  }, [open, close, popoverId]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    if (configuration.status !== "configured") { onFailed("Sign in to your workspace to attach files and skills.", true); return; }
    onPending();
    const timer = setTimeout(() => { onFailed("The workspace did not respond. Try again or open your workspace."); controller.abort(); }, 9500);
    const request = kind === "skills" ? listPersonalSkills(configuration, controller.signal).then(files => ({ files, limited: false })) : listPersonalFiles(configuration, controller.signal);
    void request.then(result => { if (!controller.signal.aborted) onComplete(result.files, result.limited); }).catch(error => {
      if (!controller.signal.aborted) onFailed(error instanceof PersonalWorkspaceError ? error.message : "Could not read workspace files.", error instanceof PersonalWorkspaceError && error.code === "workspace_auth");
    }).finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); controller.abort(); };
  }, [open, kind, configuration, refresh, onPending, onComplete, onFailed]);
  if (!open) return null;
  return <>
    <div className="mb-2 flex items-center justify-between gap-2"><p className="text-xs font-medium">{kind === "skills" ? "Skills" : "Files"}</p><button type="button" className="rounded border px-2 py-1 text-xs" disabled={status === "loading"} onMouseDown={event => event.preventDefault()} onClick={() => setRefresh(value => value + 1)}>{status === "error" ? "Retry" : "Refresh"}</button></div>
    <p className="mb-2 text-xs text-muted-foreground">Search by name or path · ↑↓ choose · Enter or Tab insert · Esc close</p>
    {configuration.status === "configured" && (status === "idle" || status === "loading") && <StatusMessage kind="loading" title="Reading workspace files…" />}
    {(status === "signin" || configuration.status !== "configured") ? <StatusMessage kind="signin" detail="Sign in to your workspace to attach files and skills." workspaceURL={configuration.status === "configured" ? configuration.url : undefined} /> : error && <StatusMessage kind="error" title="Could not read workspace files" detail={error} action={configuration.status === "configured" && <a href={configuration.url} target="_blank" rel="noopener noreferrer" className="underline">Open workspace</a>} />}
    {limited && status === "ready" && <p className="mb-2 text-xs text-muted-foreground">This list is limited to 500 files in 64 folders. You can also type an exact personal path.</p>}
    <ComposerPrimitive.TriggerPopoverItems>{items => <>
      {status === "ready" && items.length === 0 && <StatusMessage kind="empty" title={kind === "skills" ? "No matching skills. Save a skill in Workspace." : "No matching files."} />}
      {items.map((item, index) => <ComposerPrimitive.TriggerPopoverItem key={item.id} item={item} index={index} data-personal-path={item.id} className="block w-full rounded-lg px-2 py-2 text-left text-sm hover:bg-muted data-highlighted:bg-muted" onMouseDown={event => event.preventDefault()}><span className="block font-medium">{item.label}</span><span className="block break-words text-xs text-muted-foreground">{item.description}</span></ComposerPrimitive.TriggerPopoverItem>)}
    </>}</ComposerPrimitive.TriggerPopoverItems>
  </>;
}
