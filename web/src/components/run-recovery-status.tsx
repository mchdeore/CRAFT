import { RefreshCwIcon } from "lucide-react";
import type { RunRecoveryView } from "@/lib/run-recovery";

interface RunRecoveryStatusProps {
  view: RunRecoveryView;
  onRefresh: () => void | Promise<void>;
}

const statusLabels = {
  none: "None",
  running: "Running",
  paused: "Paused",
  finished: "Finished",
  failed: "Failed",
  cancelled: "Cancelled",
};
const tone = {
  none: "bg-muted-foreground/40",
  running: "bg-sky-500 animate-pulse",
  paused: "bg-amber-500",
  finished: "bg-emerald-500",
  failed: "bg-destructive",
  cancelled: "bg-muted-foreground/60",
};

/** A compact chip for the conversation's last recorded run, with a refresh control. */
export function RunRecoveryStatus({ view, onRefresh }: RunRecoveryStatusProps) {
  const checking = view.phase === "idle" || view.phase === "loading";
  const data = view.phase === "ready" ? view.data : null;
  const label = checking ? "Checking recorded run"
    : !data ? "Run status unavailable"
      : data.status === "none" ? "No recorded run"
        : `Recorded run: ${statusLabels[data.status]}`;
  const quiet = checking || data?.status === "none" || data?.status === "finished";

  return (
    <div role="status" className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
      <span className={`flex items-center gap-1.5 ${quiet ? "sr-only" : ""}`}>
        {data && <span aria-hidden className={`size-1.5 rounded-full ${tone[data.status]}`} />}
        {label}
        {data?.status === "running" && <span>(last recorded state)</span>}
        {data?.status === "paused" && data.approvalId !== null && data.approvalStatus !== null && <span>· Approval {data.approvalStatus}</span>}
      </span>
      <button type="button" aria-label="Refresh run status" title="Refresh run status" onClick={onRefresh} disabled={checking}
        className="rounded-md p-1.5 hover:bg-foreground/5 hover:text-foreground disabled:opacity-40">
        <RefreshCwIcon className={`size-3.5 ${checking ? "animate-spin" : ""}`} />
      </button>
    </div>
  );
}
