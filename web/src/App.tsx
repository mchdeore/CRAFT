import { ChatHistory } from "@/components/chat-history";
import { AgentThread } from "@/components/agent-thread";
import { PersonalSkillsWorkspace } from "@/components/personal-skills-workspace";
import { WorkspaceShell } from "@/components/workspace-shell";
import { JupyterWorkspace } from "@/components/jupyter-workspace";
import { StatusMessage } from "@/components/ui/status-message";
import { resolveUserWorkspaceURL } from "@/components/workspace-url";
import { useAgentSession } from "@/hooks/use-agent-session";
import { useAuthSession } from "@/hooks/use-auth-session";
import type { Me } from "craft-api/api/types";
import { LogOutIcon, SparklesIcon } from "lucide-react";
import { useCallback, useMemo, useRef, type FormEvent } from "react";

export default function App() {
  const auth = useAuthSession();
  const centered = "flex flex-1 items-center justify-center text-sm";
  return (
    <main className="flex h-dvh flex-col bg-background">
      {auth.error && <p role="alert" className="mx-auto mt-3 max-w-3xl px-4 text-sm text-destructive">{auth.error}</p>}
      {auth.me ? <AuthenticatedChat key={`${auth.generation}:${auth.me.id}`} owner={auth.me} generation={auth.generation} onAuthInvalid={auth.invalidate} onSignOut={() => void auth.signOut()} /> :
        auth.status === "checking" ? <div className={centered}><StatusMessage kind="loading" title="Checking sign-in..." /></div> :
        auth.status === "signing-out" ? <div role="status" className={centered}>Signing out...</div> :
        auth.status === "logout-failed" ? <div className={centered}><button type="button" className="rounded-lg border px-3 py-2 hover:bg-muted" onClick={() => void auth.signOut()}>Retry sign-out</button></div> :
        auth.status === "error" ? <div className={centered}><button type="button" className="rounded-lg border px-3 py-2 hover:bg-muted" onClick={() => void auth.retry()}>Retry sign-in check</button></div> :
        <SignInForm pending={auth.status === "signing-in"} onSubmit={auth.signIn} />}
    </main>
  );
}

function SignInForm({ pending, onSubmit }: { pending: boolean; onSubmit: (input: { username: string; password: string }) => Promise<void> }) {
  const username = useRef<HTMLInputElement>(null);
  const password = useRef<HTMLInputElement>(null);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending || !username.current || !password.current) return;
    const input = { username: username.current.value, password: password.current.value };
    password.current.value = "";
    void onSubmit(input);
  };
  const field = "h-10 w-full rounded-lg border bg-background px-3 outline-none focus:ring-2 focus:ring-ring/40";
  return <div className="flex flex-1 items-center justify-center bg-muted/30 px-4"><form className="w-full max-w-sm space-y-5 rounded-2xl border bg-background p-8 shadow-sm" onSubmit={submit} aria-label="Sign in">
    <div className="space-y-1.5">
      <p className="flex items-center gap-2 text-lg font-semibold tracking-tight"><SparklesIcon className="size-5 text-primary" />CRAFT</p>
      <p className="text-sm text-muted-foreground">Cited answers from project documents and the engineering datacube.</p>
    </div>
    <div className="space-y-2"><label htmlFor="username" className="text-sm font-medium">Username</label><input ref={username} id="username" name="username" autoComplete="username" required disabled={pending} className={field} /></div>
    <div className="space-y-2"><label htmlFor="password" className="text-sm font-medium">Password</label><input ref={password} id="password" name="password" type="password" autoComplete="current-password" required disabled={pending} className={field} /></div>
    <button type="submit" disabled={pending} className="h-10 w-full rounded-lg bg-primary px-3 font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">{pending ? "Signing in..." : "Sign in"}</button>
  </form></div>;
}

function Account({ owner, onSignOut }: { owner: Me; onSignOut: () => void }) {
  return <div className="flex items-center gap-2.5 rounded-lg px-1.5 py-1">
    <span aria-hidden className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-medium text-primary">{owner.displayName.trim().charAt(0).toUpperCase() || "?"}</span>
    <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{owner.displayName}</span><span className="block truncate text-xs text-muted-foreground">{owner.username}</span></span>
    <button type="button" aria-label="Sign out" title="Sign out" className="rounded-md p-1.5 text-muted-foreground hover:bg-foreground/5 hover:text-foreground" onClick={onSignOut}><LogOutIcon className="size-4" /></button>
  </div>;
}

function AuthenticatedChat({ owner, generation, onAuthInvalid, onSignOut }: { owner: Me; generation: number; onAuthInvalid: () => void; onSignOut: () => void }) {
  const workspace = useMemo(() => resolveUserWorkspaceURL(import.meta.env?.VITE_USER_WORKSPACES, owner.id, window.location.origin), [owner.id]);
  const {
    sessionID,
    selectedSkill,
    error,
    notice,
    renewSession,
    resetSession,
    dismissNotice,
    retrySession,
    pending,
    history,
    archivedHistory,
    historyPending,
    historyError,
    historyLimited,
    newChat,
    selectChat,
    renameChat,
    groupChat,
    archiveChat,
    restoreChat,
    refreshHistory,
  } = useAgentSession(owner, onAuthInvalid);
  // An empty chat is reused instead of stacking more empty chats in history.
  const empty = useRef(false);
  const changed = useCallback(({ empty: isEmpty, renamed }: { empty: boolean; renamed: boolean }) => {
    empty.current = isEmpty;
    if (renamed) void refreshHistory();
  }, [refreshHistory]);

  return (
    <WorkspaceShell
      files={<JupyterWorkspace configuration={workspace} />}
      skills={<PersonalSkillsWorkspace configuration={workspace} onAuthInvalid={onAuthInvalid} />}
      account={<Account owner={owner} onSignOut={onSignOut} />}
      chats={<ChatHistory
        sessionID={sessionID}
        history={history}
        archivedHistory={archivedHistory}
        pending={pending}
        historyPending={historyPending}
        historyError={historyError}
        historyLimited={historyLimited}
        onNewChat={() => { if (!empty.current) void newChat(); }}
        onSelectChat={id => void selectChat(id)}
        onRenameChat={(id, title) => void renameChat(id, title)}
        onGroupChat={(id, groupName) => void groupChat(id, groupName)}
        onArchiveChat={id => void archiveChat(id)}
        onRestoreChat={id => void restoreChat(id)}
        onRefreshHistory={() => void refreshHistory()}
        onReset={() => void resetSession()}
      />}
      chat={<>
      {error && <p role="alert" className="mx-auto mt-2 w-full max-w-3xl px-4 text-sm text-destructive">{error}</p>}
      {notice && (
        <div className="mx-auto mt-2 flex w-full max-w-3xl items-center justify-between gap-3 px-4 text-sm">
          <p>{notice}</p>
          <button type="button" className="shrink-0 rounded-md border px-2 py-1 hover:bg-muted" onClick={dismissNotice} aria-label="Dismiss session notice">Close</button>
        </div>
      )}
      {sessionID ? (
        <AgentThread
          key={`${generation}:${owner.id}:${sessionID}`}
          sessionId={sessionID}
          title={history.find(chat => chat.id === sessionID)?.title.trim() || "New chat"}
          workspace={workspace}
          selectedSkill={selectedSkill}
          onSessionInvalid={renewSession}
          onAuthInvalid={onAuthInvalid}
          onChanged={changed}
        />
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          {error ? <button type="button" className="rounded-lg border px-3 py-2 hover:bg-muted" onClick={() => void retrySession()}>Retry session</button> : <StatusMessage kind="loading" title="Initializing session..." />}
        </div>
      )}
      </>}
    />
  );
}
