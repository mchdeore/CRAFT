import { ChatHistory } from "@/components/chat-history";
import { AgentThread } from "@/components/agent-thread";
import { PersonalSkillsWorkspace } from "@/components/personal-skills-workspace";
import { WorkspaceShell } from "@/components/workspace-shell";
import { JupyterWorkspace } from "@/components/jupyter-workspace";
import { resolveUserWorkspaceURL } from "@/components/workspace-url";
import { useAgentSession } from "@/hooks/use-agent-session";
import { useAuthSession } from "@/hooks/use-auth-session";
import type { Me } from "craft-api/api/types";
import { useMemo, useRef, type FormEvent } from "react";

export default function App() {
  const auth = useAuthSession();
  return (
    <main className="flex h-dvh flex-col">
      <div className="border-b px-4 py-3">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between gap-3 text-sm">
          <span className="font-medium">CRAFT{auth.me ? ` · ${auth.me.displayName}` : ""}</span>
          {auth.me && <button type="button" className="h-9 rounded-md border px-3 hover:bg-muted" onClick={() => void auth.signOut()}>Sign out</button>}
        </div>
      </div>
      {auth.error && <p role="alert" className="mx-auto mt-3 max-w-3xl px-4 text-sm text-destructive">{auth.error}</p>}
      {auth.me ? <AuthenticatedChat key={`${auth.generation}:${auth.me.id}`} owner={auth.me} generation={auth.generation} onAuthInvalid={auth.invalidate} /> :
        auth.status === "checking" ? <div role="status" className="flex flex-1 items-center justify-center text-sm text-muted-foreground">Checking sign-in...</div> :
        auth.status === "signing-out" ? <div role="status" className="flex flex-1 items-center justify-center text-sm">Signing out...</div> :
        auth.status === "logout-failed" ? <div className="flex flex-1 items-center justify-center"><button type="button" className="rounded-md border px-3 py-2" onClick={() => void auth.signOut()}>Retry sign-out</button></div> :
        auth.status === "error" ? <div className="flex flex-1 items-center justify-center"><button type="button" className="rounded-md border px-3 py-2" onClick={() => void auth.retry()}>Retry sign-in check</button></div> :
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
  return <div className="flex flex-1 items-center justify-center px-4"><form className="w-full max-w-sm space-y-4" onSubmit={submit} aria-label="Sign in">
    <div className="space-y-2"><label htmlFor="username" className="text-sm font-medium">Username</label><input ref={username} id="username" name="username" autoComplete="username" required disabled={pending} className="h-10 w-full rounded-md border bg-background px-3" /></div>
    <div className="space-y-2"><label htmlFor="password" className="text-sm font-medium">Password</label><input ref={password} id="password" name="password" type="password" autoComplete="current-password" required disabled={pending} className="h-10 w-full rounded-md border bg-background px-3" /></div>
    <button type="submit" disabled={pending} className="h-10 w-full rounded-md border px-3 hover:bg-muted disabled:opacity-50">{pending ? "Signing in..." : "Sign in"}</button>
  </form></div>;
}

function AuthenticatedChat({ owner, generation, onAuthInvalid }: { owner: Me; generation: number; onAuthInvalid: () => void }) {
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
    historyPending,
    historyError,
    historyLimited,
    newChat,
    selectChat,
    refreshHistory,
  } = useAgentSession(owner, onAuthInvalid);

  return (
    <WorkspaceShell
      files={<JupyterWorkspace configuration={workspace} />}
      skills={<PersonalSkillsWorkspace configuration={workspace} onAuthInvalid={onAuthInvalid} />}
      chat={<>
      <ChatHistory
        sessionID={sessionID}
        history={history}
        pending={pending}
        historyPending={historyPending}
        historyError={historyError}
        historyLimited={historyLimited}
        onNewChat={() => void newChat()}
        onSelectChat={id => void selectChat(id)}
        onRefreshHistory={() => void refreshHistory()}
        onReset={() => void resetSession()}
      />
      <div className="px-4">
        {/* Render if error with sessionID  */}
        {error && (
          <p role="alert" className="mx-auto mt-2 max-w-3xl text-sm text-destructive">
            {error}
          </p>
        )}
        {/* Render if notice is set (for ex. after session refresh)  */}
        {notice && (
          <div className="mx-auto mt-2 flex max-w-3xl items-center justify-between gap-3 text-sm">
            <p>{notice}</p>
            <button
              type="button"
              className="shrink-0 rounded-md border px-2 py-1 hover:bg-muted"
              onClick={dismissNotice}
              aria-label="Dismiss session notice"
            >
              Close
            </button>
          </div>
        )}
      </div>

      {selectedSkill && <p role="status" className="mx-auto px-4 py-2 text-sm break-words">Skill: {selectedSkill.name} · revision {selectedSkill.revision}</p>}
      {sessionID ? (
        <AgentThread
          key={`${generation}:${owner.id}:${sessionID}`}
          sessionId={sessionID}
          workspace={workspace}
          selectedSkill={selectedSkill}
          onSessionInvalid={renewSession}
          onAuthInvalid={onAuthInvalid}
        />
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          {error ? <button type="button" className="rounded-md border px-3 py-2" onClick={() => void retrySession()}>Retry session</button> : "Initializing session..."}
        </div>
      )}
      </>}
    />
  );
}
