import { Button } from "@/components/ui/button";
import type { RunApprovalView } from "@/lib/run-approval";
import { FileCodeIcon, NotebookPenIcon, ShieldQuestionIcon, TerminalIcon } from "lucide-react";

interface RunApprovalPanelProps {
  view: RunApprovalView;
  onRefresh: () => void | Promise<void>;
  onDecide: (approve: boolean) => void | Promise<void>;
  onResume: () => void | Promise<void>;
}

const text = (value: unknown) => typeof value === "string" ? value : value === undefined ? "" : JSON.stringify(value, null, 2);

/** The proposed change, shown the way a person would review it: code as code, files with their path. */
function Proposal({ tool, args }: { tool: string; args: Record<string, unknown> }) {
  const code = "max-h-72 overflow-auto rounded-lg bg-muted/60 p-3 font-mono text-xs leading-5 whitespace-pre";
  if (tool === "run_code") return <div className="space-y-1.5">
    <p className="flex items-center gap-1.5 font-medium"><TerminalIcon className="size-4" />Run Python in your workspace</p>
    <pre className={code}>{text(args.code)}</pre>
  </div>;
  if (tool === "write_file") return <div className="space-y-1.5">
    <p className="flex items-center gap-1.5 font-medium"><FileCodeIcon className="size-4" />Write <code className="rounded bg-muted px-1 font-mono text-xs">{text(args.path)}</code></p>
    <pre className={code}>{text(args.content)}</pre>
  </div>;
  if (tool === "edit_notebook") return <div className="space-y-1.5">
    <p className="flex items-center gap-1.5 font-medium"><NotebookPenIcon className="size-4" />{text(args.action)} cell {text(args.index)} in <code className="rounded bg-muted px-1 font-mono text-xs">{text(args.path)}</code></p>
    {args.action !== "delete" && <pre className={code}>{text(args.source)}</pre>}
  </div>;
  return <pre className={code}>{JSON.stringify(args, null, 2)}</pre>;
}

export function RunApprovalPanel({ view, onRefresh, onDecide, onResume }: RunApprovalPanelProps) {
  if (view.phase === "idle") return null;
  if (view.phase === "loading") return <p role="status" className="px-1 text-sm text-muted-foreground">Loading approval…</p>;
  if (view.phase !== "ready" || !view.data) return (
    <div role="status" className="flex flex-wrap items-center gap-2 px-1 text-sm">
      <span>Approval unavailable.</span>
      <Button variant="outline" size="sm" disabled={view.busy !== null} onClick={() => { void onRefresh(); }}>Refresh approval</Button>
    </div>
  );
  const data = view.data;
  const busy = view.busy !== null;
  const pending = data.status === "pending";
  return (
    <section aria-label="Current approval" className={`rounded-2xl border bg-background p-4 text-sm shadow-sm ${pending ? "border-amber-500/50 ring-1 ring-amber-500/20" : ""}`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase"><ShieldQuestionIcon className="size-3.5" />{pending ? "Approval needed" : `Approval ${data.status}`} · {data.tool}</p>
          <p className="mt-1 break-words">{data.summary}</p>
        </div>
        <Button variant="ghost" size="sm" disabled={busy} onClick={() => { void onRefresh(); }}>Refresh approval</Button>
      </div>
      <Proposal tool={data.tool} args={data.arguments} />
      <details className="mt-3 text-muted-foreground">
        <summary className="cursor-pointer select-none hover:text-foreground">Review details</summary>
        <dl className="mt-2 space-y-2 text-foreground">
          <dt className="font-medium">Arguments</dt><dd><pre className="whitespace-pre-wrap break-words text-xs">{JSON.stringify(data.arguments, null, 2)}</pre></dd>
          <dt className="font-medium">Reasoning</dt><dd className="whitespace-pre-wrap break-words">{data.reasoning}</dd>
          <dt className="font-medium">Evidence</dt><dd><pre className="whitespace-pre-wrap break-words text-xs">{JSON.stringify(data.evidence, null, 2)}</pre></dd>
          <dt className="font-medium">Retrieval path</dt><dd><pre className="whitespace-pre-wrap break-words text-xs">{JSON.stringify(data.retrievalPath, null, 2)}</pre></dd>
          <dt className="font-medium">Events</dt><dd><ul>{data.events.map((event, index) => <li key={index}>{event.kind} · {event.at}</li>)}</ul></dd>
        </dl>
      </details>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {pending && <>
          <Button size="sm" disabled={busy || !view.canDecide} onClick={() => { void onDecide(true); }}>Approve</Button>
          <Button variant="outline" size="sm" disabled={busy || !view.canDecide} onClick={() => { void onDecide(false); }}>Reject</Button>
        </>}
        {(data.status === "approved" || data.status === "rejected") && (
          <Button size="sm" disabled={busy || !view.canResume} onClick={() => { void onResume(); }}>
            {data.status === "approved" ? "Continue run" : "Continue without this change"}
          </Button>
        )}
        {busy && <span role="status" className="text-muted-foreground">{view.busy === "decision" ? "Saving decision…" : "Continuing run…"}</span>}
      </div>
    </section>
  );
}
