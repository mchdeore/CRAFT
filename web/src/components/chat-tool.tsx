import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { toolLabel, toolView } from "@/lib/chat-evidence";

const dot = { Running: "bg-sky-500 animate-pulse", Completed: "bg-emerald-500", Failed: "bg-destructive", "Awaiting review": "bg-amber-500", Unavailable: "bg-muted-foreground/40" };

export function ChatTool(props: ToolCallMessagePartProps) {
  const view = toolView(props);
  return (
    <div className="flex items-center gap-2 py-1 text-xs text-muted-foreground" role="status" aria-busy={view.status === "Running"} title={view.name}>
      <span aria-hidden className={`size-1.5 shrink-0 rounded-full ${dot[view.status]}`} />
      <span className="text-foreground/80">{toolLabel([view.name])}</span>
      <span className="sr-only">{view.name}</span>
      <span className="ml-auto">{view.status}</span>
      {view.status === "Awaiting review" && <span>· waiting for your review</span>}
    </div>
  );
}
