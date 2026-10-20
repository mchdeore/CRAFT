import { Button } from "@/components/ui/button";
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

export function RunRecoveryStatus({ view, onRefresh }: RunRecoveryStatusProps) {
  const checking = view.phase === "idle" || view.phase === "loading";
  const data = view.phase === "ready" ? view.data : null;
  const label = checking ? "Checking recorded run"
    : !data ? "Run status unavailable"
      : data.status === "none" ? "No recorded run"
        : `Recorded run: ${statusLabels[data.status]}`;

  return (
    <div role="status" className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      {data?.status === "running" && (
        <span className="text-xs text-muted-foreground">(last recorded state)</span>
      )}
      {data?.status === "paused" && data.approvalId !== null && data.approvalStatus !== null && (
        <span className="text-muted-foreground">Approval {data.approvalStatus}</span>
      )}
      <Button type="button" variant="outline" size="sm" onClick={onRefresh} disabled={checking}>
        Refresh run status
      </Button>
    </div>
  );
}
