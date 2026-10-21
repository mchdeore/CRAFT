import type { ConversationSummary } from "craft-api/api/types";
import { ArchiveIcon, FolderIcon, PencilIcon, PlusIcon, RefreshCwIcon, RotateCcwIcon } from "lucide-react";
import { useState } from "react";

interface ChatHistoryProps {
  sessionID: string;
  history: ConversationSummary[];
  archivedHistory: ConversationSummary[];
  pending: boolean;
  historyPending: boolean;
  historyError?: string;
  historyLimited: boolean;
  onNewChat: () => void;
  onSelectChat: (id: string) => void;
  onRenameChat: (id: string, title: string) => void;
  onGroupChat: (id: string, groupName: string | null) => void;
  onArchiveChat: (id: string) => void;
  onRestoreChat: (id: string) => void;
  onRefreshHistory: () => void;
  onReset: () => void;
}

/** Default titles gain their time; repeated labels gain an ordinal. */
function chatLabels(chats: ConversationSummary[]): string[] {
  const titles = chats.map(chat => chat.title.trim() || "Untitled chat");
  const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "short" });
  const labels = chats.map((chat, index) => {
    const title = titles[index];
    return title === "New conversation" || title === "New chat"
      ? `${title} · ${dateFormat.format(new Date(chat.updatedAt))}` : title;
  });
  const labelCounts = new Map<string, number>();
  for (const label of labels) labelCounts.set(label, (labelCounts.get(label) ?? 0) + 1);
  const ordinals = new Map<string, number>();
  return labels.map(label => {
    if ((labelCounts.get(label) ?? 0) < 2) return label;
    const ordinal = (ordinals.get(label) ?? 0) + 1;
    ordinals.set(label, ordinal);
    return `${label} · #${ordinal}`;
  });
}

const iconButton = "rounded-md p-1.5 text-muted-foreground hover:bg-foreground/5 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40";

export function ChatHistory({
  sessionID,
  history,
  archivedHistory,
  pending,
  historyPending,
  historyError,
  historyLimited,
  onNewChat,
  onSelectChat,
  onRenameChat,
  onGroupChat,
  onArchiveChat,
  onRestoreChat,
  onRefreshHistory,
  onReset,
}: ChatHistoryProps) {
  const [showArchived, setShowArchived] = useState(false);
  const available = history.filter(chat => !chat.archived);
  const archived = archivedHistory.filter(chat => chat.archived);
  const labels = chatLabels(available);
  const archivedLabels = chatLabels(archived);
  const hasSelected = available.some(chat => chat.id === sessionID);
  const rename = (chat: ConversationSummary) => {
    const proposed = window.prompt("Rename chat", chat.title);
    if (proposed === null) return;
    const title = proposed.trim();
    if (!title || title === chat.title) return;
    onRenameChat(chat.id, title);
  };
  const group = (chat: ConversationSummary) => {
    const proposed = window.prompt("Group name (leave empty to clear group)", chat.groupName ?? "");
    if (proposed === null) return;
    const groupName = proposed.trim();
    onGroupChat(chat.id, groupName || null);
  };
  return <div className="flex min-h-0 flex-1 flex-col">
    <div className="px-2">
      <button type="button" className="flex w-full items-center gap-2 rounded-lg border bg-background px-2.5 py-1.5 text-sm font-medium shadow-xs hover:bg-foreground/5 disabled:cursor-not-allowed disabled:opacity-50" onClick={onNewChat} disabled={pending}>
        <PlusIcon className="size-4" />New chat
      </button>
    </div>
    <div className="flex items-center justify-between px-4 pt-4 pb-1">
      <span id="chat-history-label" className="text-xs font-medium text-muted-foreground">Chat history</span>
      <button type="button" aria-label={historyError ? "Retry history" : "Refresh history"} title={historyError ? "Retry history" : "Refresh history"} className={iconButton} onClick={onRefreshHistory} disabled={historyPending || pending}>
        <RefreshCwIcon className={`size-3.5 ${historyPending ? "animate-spin" : ""}`} />
      </button>
    </div>
    <ul id="chat-history" aria-labelledby="chat-history-label" className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
      {sessionID && !hasSelected && <li><span aria-current="true" className="block truncate rounded-lg bg-foreground/[0.07] px-2.5 py-1.5 text-sm font-medium">Current chat</span></li>}
      {available.map((chat, index) => {
        const current = chat.id === sessionID;
        return <li key={chat.id} className="group relative">
          <button type="button" data-chat={chat.id} aria-current={current ? "true" : undefined} disabled={pending} title={labels[index]} onClick={() => { if (!current) onSelectChat(chat.id); }}
            className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 pr-24 text-left text-sm disabled:cursor-not-allowed ${current ? "bg-foreground/[0.07] font-medium" : "text-foreground/80 hover:bg-foreground/5"}`}>
            <span className="min-w-0 flex-1 truncate">{labels[index]}</span>
            {chat.groupName && <span className="rounded bg-foreground/[0.06] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">{chat.groupName}</span>}
            {chat.pendingApprovals > 0 && <span className="size-2 shrink-0 rounded-full bg-amber-500" aria-label="Waiting for approval" />}
          </button>
          <div className="absolute top-1 right-1 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
            <button type="button" aria-label="Rename chat" title="Rename chat" className={iconButton} onClick={() => rename(chat)} disabled={pending}>
              <PencilIcon className="size-3.5" />
            </button>
            <button type="button" aria-label={chat.groupName ? "Edit group" : "Set group"} title={chat.groupName ? "Edit group" : "Set group"} className={iconButton} onClick={() => group(chat)} disabled={pending}>
              <FolderIcon className="size-3.5" />
            </button>
            {current ? <button type="button" aria-label="Archive and reset" title="Archive and reset" className={iconButton} onClick={onReset} disabled={pending}>
              <ArchiveIcon className="size-3.5" />
            </button> : <button type="button" aria-label="Archive chat" title="Archive chat" className={iconButton} onClick={() => onArchiveChat(chat.id)} disabled={pending}>
              <ArchiveIcon className="size-3.5" />
            </button>}
          </div>
        </li>;
      })}
      {!available.length && !historyPending && !sessionID && <li className="px-2.5 py-1.5 text-sm text-muted-foreground">{pending ? "Opening chat..." : "No chats yet"}</li>}
    </ul>
    {!!archived.length && <div className="px-4 pb-2">
      <button
        type="button"
        className="text-xs font-medium text-muted-foreground underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:opacity-50"
        onClick={() => setShowArchived(open => !open)}
        disabled={pending}
      >
        {showArchived ? "Hide archived chats" : `Show archived chats (${archived.length})`}
      </button>
    </div>}
    {showArchived && <ul aria-label="Archived chats" className="space-y-0.5 px-2 pb-2">
      {archived.map((chat, index) => <li key={chat.id} className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-muted-foreground">
        <span className="min-w-0 flex-1 truncate" title={archivedLabels[index]}>{archivedLabels[index]}</span>
        <button type="button" className="inline-flex items-center gap-1 rounded-md border bg-background px-2 py-1 text-xs font-medium text-foreground hover:bg-foreground/5 disabled:cursor-not-allowed disabled:opacity-40" onClick={() => onRestoreChat(chat.id)} disabled={pending}>
          <RotateCcwIcon className="size-3" />Restore
        </button>
      </li>)}
      {!archived.length && <li className="px-2.5 py-1.5 text-sm text-muted-foreground">No archived chats</li>}
    </ul>}
    {historyPending && <p role="status" className="px-4 pb-2 text-xs text-muted-foreground">Loading chat history...</p>}
    {historyError && <p role="alert" className="px-4 pb-2 text-xs text-destructive">{historyError}</p>}
    {historyLimited && <p className="px-4 pb-2 text-xs text-muted-foreground">Chat history is limited to 100 entries.</p>}
  </div>;
}
