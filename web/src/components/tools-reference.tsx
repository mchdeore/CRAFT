import { useEffect, useState } from "react";
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
const channelLabels: Record<ToolFilter, string> = {
  all: "All channels", api: "API", mcp: "MCP", chat: "Chat", read_only_query: "Read-only query",
};

export function ToolReferenceView({ tools, query = "", channel = "all", status = "ready", onQueryChange, onChannelChange, onRetry }: ViewProps) {
  const filtered = filterTools(tools, query, channel);
  return <section aria-label="Tools reference" className="min-h-0 flex-1 overflow-y-auto px-4 py-5">
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-lg font-medium">Tools reference</h1>
      <p className="text-sm text-muted-foreground">Declared channels show where tools are exposed. Access checks, approvals and service configuration still apply.</p>
      {status === "loading" ? <p role="status">Loading tool reference...</p> : status === "error" ?
        <div className="space-y-2"><p role="alert">Could not load tool reference.</p><button type="button" className="rounded-md border px-3 py-2" onClick={onRetry}>Retry</button></div> : <>
          <div className="flex flex-wrap gap-3">
            <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm">Filter tools<input className="rounded-md border bg-background px-3 py-2" value={query} onChange={event => onQueryChange?.(event.target.value)} placeholder="Name or description" /></label>
            <label className="flex flex-col gap-1 text-sm">Channel<select className="rounded-md border bg-background px-3 py-2" value={channel} onChange={event => onChannelChange?.(event.target.value as ToolFilter)}>
              {Object.entries(channelLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select></label>
          </div>
          <p className="text-sm text-muted-foreground">{filtered.length} of {tools.length} tools</p>
          {tools.length === 0 ? <p role="status">No tools were returned.</p> : filtered.length === 0 ? <p role="status">No matching tools.</p> : filtered.map(tool => <article key={tool.name} className="space-y-2 rounded-md border p-4">
            <h2 className="break-all font-medium">{tool.name}</h2>
            <p className="whitespace-pre-wrap text-sm">{tool.description}</p>
            <p className="break-all text-sm"><code>{tool.route}</code></p>
            <p className="text-sm">Declared channels: {tool.availableIn.map(value => channelLabels[value]).join(", ")}</p>
            <p className="text-sm">Effects: {tool.effects}{tool.needsApproval ? " · Agent approval required" : ""}</p>
            <details><summary className="cursor-pointer text-sm">Input schema</summary><pre className="mt-2 max-h-72 overflow-auto rounded bg-muted p-3 text-xs"><code>{JSON.stringify(tool.inputSchema, null, 2)}</code></pre></details>
            <details><summary className="cursor-pointer text-sm">Output schema</summary>{tool.outputSchema === null ? <p className="mt-2 text-sm">Native JSON schema unavailable.</p> : <pre className="mt-2 max-h-72 overflow-auto rounded bg-muted p-3 text-xs"><code>{JSON.stringify(tool.outputSchema, null, 2)}</code></pre>}</details>
          </article>)}
        </>}
    </div>
  </section>;
}

export function ToolsReference({ onAuthInvalid }: { onAuthInvalid: () => void }) {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [status, setStatus] = useState<Status>("loading");
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  const [channel, setChannel] = useState<ToolFilter>("all");
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
  return <ToolReferenceView tools={tools} query={query} channel={channel} status={status} onQueryChange={setQuery} onChannelChange={setChannel} onRetry={retry} />;
}
