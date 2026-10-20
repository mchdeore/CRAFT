import { Button } from "@/components/ui/button";
import type { RunApprovalView } from "@/lib/run-approval";

interface RunApprovalPanelProps {
  view: RunApprovalView;
  onRefresh: () => void | Promise<void>;
  onDecide: (approve: boolean) => void | Promise<void>;
  onResume: () => void | Promise<void>;
}

export function RunApprovalPanel({ view, onRefresh, onDecide, onResume }: RunApprovalPanelProps) {
  if (view.phase === "idle") return null;
  if (view.phase === "loading") return <p role="status" className="px-4 py-2 text-sm text-muted-foreground">Loading approval…</p>;
  if (view.phase !== "ready" || !view.data) return (
    <div role="status" className="flex flex-wrap items-center gap-2 px-4 py-2 text-sm">
      <span>Approval unavailable.</span>
      <Button variant="outline" size="sm" disabled={view.busy !== null} onClick={() => { void onRefresh(); }}>Refresh approval</Button>
    </div>
  );
  const data = view.data;
  const busy = view.busy !== null;
  return (
    <section aria-label="Current approval" className="mx-3 mt-2 rounded-lg border p-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 break-words"><p className="font-medium">{data.tool}</p><p>{data.summary}</p><p className="text-muted-foreground">{data.status}</p></div>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => { void onRefresh(); }}>Refresh approval</Button>
      </div>
      <details className="mt-2">
        <summary className="cursor-pointer">Review details</summary>
        <dl className="mt-2 space-y-2">
          <dt className="font-medium">Arguments</dt><dd><pre className="whitespace-pre-wrap break-words">{JSON.stringify(data.arguments, null, 2)}</pre></dd>
          <dt className="font-medium">Reasoning</dt><dd className="whitespace-pre-wrap break-words">{data.reasoning}</dd>
          <dt className="font-medium">Evidence</dt><dd><pre className="whitespace-pre-wrap break-words">{JSON.stringify(data.evidence, null, 2)}</pre></dd>
          <dt className="font-medium">Retrieval path</dt><dd><pre className="whitespace-pre-wrap break-words">{JSON.stringify(data.retrievalPath, null, 2)}</pre></dd>
          <dt className="font-medium">Events</dt><dd><ul>{data.events.map((event, index) => <li key={index}>{event.kind} · {event.at}</li>)}</ul></dd>
        </dl>
      </details>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {data.status === "pending" && <>
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
