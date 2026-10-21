import { useEffect, useState } from "react";
import { StatusMessage } from "@/components/ui/status-message";
import { toolLabel } from "@/lib/chat-evidence";
import type { ToolInfo } from "craft-api/api/types";
import { fetchToolReference, filterTools, ToolReferenceError, type ToolFilter } from "@/lib/tools-reference";

type Status = "loading" | "ready" | "error";
type ViewProps = {
  tools: ToolInfo[];
  query?: string;
  channel?: ToolFilter;
  status?: Status;
  onQueryChange?: (value: string) => void;
  onChannelChange?: (value: ToolFilter) => void;
  onRetry?: () => void;
};
export function ToolReferenceRow({ tool, disabled, onInsert }: { tool: ToolInfo; disabled?: boolean; onInsert?: () => void }) {
  return <article className="space-y-1 rounded-md border bg-background p-3">
    <div className="flex items-start justify-between gap-2"><h2 className="text-sm font-medium">{toolLabel([tool.name])}</h2>{onInsert && <button type="button" aria-label={`Insert /${tool.name}`} className="shrink-0 text-xs underline disabled:opacity-50" disabled={disabled} onMouseDown={event => event.preventDefault()} onClick={onInsert}>Insert</button>}</div>
    <p className="truncate text-xs text-muted-foreground" title={tool.description}>{tool.description.split("\n", 1)[0]?.replaceAll("USER_ROOT", "your workspace")}</p>
    <details><summary className="cursor-pointer text-xs text-muted-foreground">Parameters</summary>
      <p className="mt-2 text-xs"><code>{tool.name}</code>{tool.needsApproval && " · Approval required"}{!tool.availableIn.includes("chat") && " · Not available in chat"}</p>
      <pre className="mt-2 max-h-48 overflow-auto rounded bg-muted p-2 text-xs"><code>{JSON.stringify(tool.inputSchema, null, 2)}</code></pre>
    </details>
  </article>;
}

export function ToolReferenceView({ tools, query = "", channel = "all", status = "ready", onQueryChange, onRetry }: ViewProps) {
  const filtered = filterTools(tools, query, channel);
  return <section aria-label="Tools reference" className="min-h-0 flex-1 overflow-y-auto px-4 py-5">
    <div className="mx-auto max-w-3xl space-y-3">
      <h2 className="text-base font-medium">Tools</h2>
      <label className="flex flex-col gap-1 text-sm">Search tools<input type="search" className="rounded-md border bg-background px-3 py-2" value={query} onChange={event => onQueryChange?.(event.target.value)} placeholder="Name or description" /></label>
      {status === "loading" ? <StatusMessage kind="loading" title="Loading tools…" /> : status === "error" ? <StatusMessage kind="error" title="Could not load tools" detail="Try again to refresh the tool list." action={<button type="button" className="rounded-md border px-3 py-2" onClick={onRetry}>Retry</button>} /> : <>
        <p className="text-xs text-muted-foreground">{filtered.length} of {tools.length} tools</p>
        {!filtered.length ? <StatusMessage kind="empty" title={tools.length ? "No matching tools." : "No tools available."} /> : filtered.map(tool => <ToolReferenceRow key={tool.name} tool={tool} />)}
      </>}
    </div>
  </section>;
}

export function ToolsReference({ onAuthInvalid }: { onAuthInvalid: () => void }) {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [status, setStatus] = useState<Status>("loading");
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    void fetchToolReference(controller.signal).then(result => {
      if (current) { setTools(result); setStatus("ready"); }
    }).catch(error => {
      if (!current || controller.signal.aborted) return;
      if (error instanceof ToolReferenceError && error.code === "unauthenticated") onAuthInvalid();
      else setStatus("error");
    });
    return () => { current = false; controller.abort(); };
  }, [attempt, onAuthInvalid]);
  const retry = () => { setStatus("loading"); setAttempt(value => value + 1); };
  return <ToolReferenceView tools={tools} query={query} status={status} onQueryChange={setQuery} onRetry={retry} />;
}
