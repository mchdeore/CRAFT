import { useCitations } from "@/components/citations/citation-context";
import { Button } from "@/components/ui/button";
import { listWorkspaceFiles, workspaceFileURL } from "@/lib/workspace-files";
import { useAuiState } from "@assistant-ui/react";
import type { WorkspaceFile } from "craft-api/api/types";
import {
  FileIcon,
  FileSpreadsheetIcon,
  FileTextIcon,
  FolderOpenIcon,
  PresentationIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

// The conversation's published files, in a card opened from a button above
// the thread. Files only appear during a run, so the list is
// loaded when the thread opens and again whenever a run finishes.
export function WorkspaceFiles({ conversationId }: { conversationId: string }) {
  const { openSource } = useCitations();
  const isRunning = useAuiState((state) => state.thread.isRunning);
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isRunning) return;
    let cancelled = false;
    listWorkspaceFiles(conversationId)
      .then((listed) => {
        if (cancelled) return;
        setFiles(listed);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, isRunning]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !cardRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  const openFile = (file: WorkspaceFile) => {
    const url = workspaceFileURL(conversationId, file.id);
    openSource({
      title: file.name,
      subtitle: `${typeLabel(file.mediaType)} · published by the agent`,
      mediaType: file.mediaType,
      previewURL: url,
      downloadURL: url,
    });
    setOpen(false);
  };

  return (
    <div ref={cardRef} className="relative z-20">
      <Button
        type="button"
        variant="outline"
        size="sm"
        aria-expanded={open}
        aria-label="Outputs"
        onClick={() => setOpen((value) => !value)}
      >
        <FolderOpenIcon />
        Outputs
        {files.length > 0 && (
          <span className="bg-primary text-primary-foreground rounded-full px-1.5 text-xs">
            {files.length}
          </span>
        )}
      </Button>

      {open && (
        <div
          role="dialog"
          aria-label="Outputs"
          className="bg-popover text-popover-foreground absolute right-0 mt-2 w-80 rounded-xl border p-2 shadow-lg"
        >
          <p className="text-muted-foreground px-2 pb-1 pt-1 text-xs font-medium">Outputs</p>
          {error ? (
            <p className="text-destructive px-2 py-2 text-sm">{error}</p>
          ) : files.length === 0 ? (
            <p className="text-muted-foreground px-2 py-2 text-sm">No outputs yet</p>
          ) : (
            <ul>
              {files.map((file) => {
                const Icon = typeIcon(file.mediaType);
                return (
                  <li key={file.id}>
                    <button
                      type="button"
                      className="hover:bg-accent flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm"
                      onClick={() => openFile(file)}
                    >
                      <Icon className="text-muted-foreground size-4 shrink-0" />
                      <span className="min-w-0 flex-1 truncate">{file.name}</span>
                      <span className="text-muted-foreground shrink-0 text-xs">
                        {typeLabel(file.mediaType)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function typeLabel(mediaType: string): string {
  if (mediaType.includes("wordprocessingml")) return "Word";
  if (mediaType.includes("spreadsheetml")) return "Excel";
  if (mediaType.includes("presentationml")) return "PowerPoint";
  if (mediaType === "application/pdf") return "PDF";
  return "File";
}

function typeIcon(mediaType: string) {
  if (mediaType.includes("spreadsheetml")) return FileSpreadsheetIcon;
  if (mediaType.includes("presentationml")) return PresentationIcon;
  if (mediaType.includes("wordprocessingml") || mediaType === "application/pdf") return FileTextIcon;
  return FileIcon;
}
