import type { ReactNode } from "react"

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return <header className="flex min-h-16 shrink-0 items-center justify-between gap-4 border-b px-4 py-3">
    <div className="min-w-0"><h1 className="text-lg font-semibold">{title}</h1>{description && <p className="text-sm text-muted-foreground">{description}</p>}</div>
    {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
  </header>
}
