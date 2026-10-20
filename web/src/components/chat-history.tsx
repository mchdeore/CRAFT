import type { ConversationSummary } from "craft-api/api/types";

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
export function ChatHistory({ sessionID, history, pending, historyPending, historyError, historyLimited, onNewChat, onSelectChat, onRefreshHistory, onReset }: ChatHistoryProps) {
  const available = history.filter(chat => !chat.archived);
  const hasSelected = available.some(chat => chat.id === sessionID);
  const titles = available.map(chat => chat.title.trim() || "Untitled chat");
  const titleCounts = new Map<string, number>();
  for (const title of titles) titleCounts.set(title, (titleCounts.get(title) ?? 0) + 1);
  const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "short" });
  const labels = available.map((chat, index) => {
    const title = titles[index];
    return title === "New conversation" || (titleCounts.get(title) ?? 0) > 1
      ? `${title} · ${dateFormat.format(new Date(chat.updatedAt))}` : title;
  });
  const labelCounts = new Map<string, number>();
  for (const label of labels) labelCounts.set(label, (labelCounts.get(label) ?? 0) + 1);
  const ordinals = new Map<string, number>();
  const displayLabels = labels.map(label => {
    if ((labelCounts.get(label) ?? 0) < 2) return label;
    const ordinal = (ordinals.get(label) ?? 0) + 1;
    ordinals.set(label, ordinal);
    return `${label} · #${ordinal}`;
  });
  const buttonClass = "h-9 shrink-0 rounded-md border px-3 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50";
  return <div className="border-b px-4 py-3">
    <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-3 text-sm">
      <div className="flex w-full min-w-0 flex-col gap-2 sm:w-auto sm:flex-1 sm:flex-row sm:items-center sm:gap-3">
        <label htmlFor="chat-history" className="shrink-0 font-medium">Chat history</label>
        <select id="chat-history" value={sessionID} disabled={pending} onChange={event => { if (event.target.value) onSelectChat(event.target.value); }} className="h-9 w-full min-w-0 rounded-md border bg-background px-3 outline-none focus:ring-2 focus:ring-ring sm:flex-1">
          {!sessionID && <option value="">{pending ? "Opening chat..." : "Choose a chat"}</option>}
          {sessionID && !hasSelected && <option value={sessionID}>Current chat</option>}
          {available.map((chat, index) => <option key={chat.id} value={chat.id}>{displayLabels[index]}</option>)}
        </select>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className={buttonClass} onClick={onNewChat} disabled={pending}>New chat</button>
        <button type="button" className={buttonClass} onClick={onRefreshHistory} disabled={historyPending || pending}>{historyError ? "Retry history" : "Refresh history"}</button>
        <button type="button" className={buttonClass} onClick={onReset} disabled={pending || !sessionID}>Archive and reset</button>
      </div>
    </div>
    {historyPending && <p role="status" className="mx-auto mt-2 max-w-3xl text-sm text-muted-foreground">Loading chat history...</p>}
    {historyError && <p role="alert" className="mx-auto mt-2 max-w-3xl text-sm text-destructive">{historyError}</p>}
    {historyLimited && <p className="mx-auto mt-2 max-w-3xl text-sm text-muted-foreground">Chat history is limited to 100 entries.</p>}
  </div>;
}
