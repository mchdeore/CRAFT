import { AlertCircleIcon, FolderIcon, Loader2Icon, LogInIcon } from "lucide-react"
import type { ReactNode } from "react"

export function StatusMessage({ kind, title, detail, action, icon, workspaceURL }: { kind: "loading" | "error" | "empty" | "signin"; title?: string; detail?: string; action?: ReactNode; icon?: ReactNode; workspaceURL?: string }) {
  const Icon = kind === "loading" ? Loader2Icon : kind === "error" ? AlertCircleIcon : kind === "signin" ? LogInIcon : FolderIcon
  return <div role={kind === "error" ? "alert" : "status"} className="flex items-start gap-2 py-2 text-sm">
    {icon ?? <Icon aria-hidden="true" className={`mt-0.5 size-4 shrink-0 text-muted-foreground ${kind === "loading" ? "animate-spin" : ""}`} />}
    <div className="min-w-0 space-y-1"><p className="font-medium">{title ?? (kind === "signin" ? "Sign in to your workspace" : "")}</p>{detail && <p className="text-muted-foreground">{detail}</p>}
      {kind === "signin" && workspaceURL && <a href={workspaceURL} target="_blank" rel="noopener noreferrer" className="inline-flex rounded-md border px-3 py-1.5 hover:bg-muted">Sign in to workspace</a>}
      {action}
    </div>
  </div>
}
