import type { ConversationSummary } from "craft-api/api/types";
import { ArchiveIcon, PlusIcon, RefreshCwIcon } from "lucide-react";

interface ChatHistoryProps {
  sessionID: string;
  history: ConversationSummary[];
  pending: boolean;
  historyPending: boolean;
  historyError?: string;
  historyLimited: boolean;
  onNewChat: () => void;
  onSelectChat: (id: string) => void;
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

export function ChatHistory({ sessionID, history, pending, historyPending, historyError, historyLimited, onNewChat, onSelectChat, onRefreshHistory, onReset }: ChatHistoryProps) {
  const available = history.filter(chat => !chat.archived);
  const labels = chatLabels(available);
  const hasSelected = available.some(chat => chat.id === sessionID);
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
            className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 pr-8 text-left text-sm disabled:cursor-not-allowed ${current ? "bg-foreground/[0.07] font-medium" : "text-foreground/80 hover:bg-foreground/5"}`}>
            <span className="min-w-0 flex-1 truncate">{labels[index]}</span>
            {chat.pendingApprovals > 0 && <span className="size-2 shrink-0 rounded-full bg-amber-500" aria-label="Waiting for approval" />}
          </button>
          {current && <button type="button" aria-label="Archive and reset" title="Archive and reset" className={`${iconButton} absolute top-1 right-1`} onClick={onReset} disabled={pending}>
            <ArchiveIcon className="size-3.5" />
          </button>}
        </li>;
      })}
      {!available.length && !historyPending && !sessionID && <li className="px-2.5 py-1.5 text-sm text-muted-foreground">{pending ? "Opening chat..." : "No chats yet"}</li>}
    </ul>
    {historyPending && <p role="status" className="px-4 pb-2 text-xs text-muted-foreground">Loading chat history...</p>}
    {historyError && <p role="alert" className="px-4 pb-2 text-xs text-destructive">{historyError}</p>}
    {historyLimited && <p className="px-4 pb-2 text-xs text-muted-foreground">Chat history is limited to 100 entries.</p>}
  </div>;
}
