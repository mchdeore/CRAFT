import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { toolView } from "@/lib/chat-evidence";

export function ChatTool(props: ToolCallMessagePartProps) {
  const view = toolView(props);
  return (
    <div className="bg-muted/25 my-2 rounded-lg border px-3 py-2 text-sm" role="status" aria-busy={view.status === "Running"}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium">{view.name}</span>
        <span className="text-muted-foreground text-xs">{view.status}</span>
      </div>
      {view.correlationId && <p className="text-muted-foreground mt-1 truncate text-xs">Call {view.correlationId}</p>}
      {view.status === "Awaiting review" && <p className="text-muted-foreground mt-1 text-xs">This tool is waiting for review.</p>}
    </div>
  );
}
