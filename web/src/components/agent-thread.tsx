import type { SkillSelection } from "@/lib/skills";
import type { WorkspaceConfiguration } from "@/components/workspace-url";
import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { CitationProvider } from "@/components/citations/citation-context";
import { SourcePreviewPanel } from "@/components/citations/preview/source-preview-panel";
import { RunRecoveryStatus } from "@/components/run-recovery-status";
import { RunApprovalPanel } from "@/components/run-approval-panel";
import { WorkspaceFiles } from "@/components/workspace/workspace-files";
import { PersonalChatWorkspaceProvider, PersonalChatPreviewPanel } from "@/components/personal-chat-workspace";
import { createThreadSession } from "@/lib/agent-history";
import { RunRecoveryController } from "@/lib/run-recovery";
import { RunApprovalController } from "@/lib/run-approval";
import {
  AssistantRuntimeProvider,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import { AlertCircleIcon, RotateCcwIcon } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import type { RunRecoveryView } from "@/lib/run-recovery";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { memo, useEffect, useMemo, useState, useSyncExternalStore } from "react";

interface AgentThreadProps {
  sessionId: string;
  /** The conversation title shown in the chat header. */
  title?: string;
  selectedSkill?: SkillSelection;
  workspace?: WorkspaceConfiguration;
  onSessionInvalid: () => void | Promise<void>;
  onAuthInvalid: () => void;
  /** Called when the chat gains its first message or a title. */
  onChanged?: (state: { empty: boolean; renamed: boolean }) => void;
}

export const AgentThread = memo(function AgentThread({ sessionId, title = "New chat", selectedSkill, workspace, onSessionInvalid, onAuthInvalid, onChanged }: AgentThreadProps) {
  const [error, setError] = useState<string>();
  const recovery = useMemo(() => new RunRecoveryController(sessionId, { onSessionInvalid, onAuthInvalid }), [sessionId, onSessionInvalid, onAuthInvalid]);
  const recoveryView = useSyncExternalStore(recovery.subscribe, recovery.getSnapshot, recovery.getSnapshot);
  const resources = useMemo(() => createThreadSession(sessionId, { onSessionInvalid, onAuthInvalid, onError: setError, onRunSettled: recovery.refresh }, selectedSkill), [sessionId, selectedSkill, onSessionInvalid, onAuthInvalid, recovery]);
  const { agent, history } = resources;

  // create AgUi runtime.
  const runtime = useAgUiRuntime({
    agent,
    adapters: { history },

  });
  const approval = useMemo(() => new RunApprovalController(sessionId, {
    onAuthInvalid, onRecoveryRefresh: recovery.refresh,
    onResume: (runId, approvalId) => resources.resumeApproval(runId, approvalId, repository => runtime.thread.import(repository)),
  }), [sessionId, onAuthInvalid, recovery, resources, runtime]);
  const approvalView = useSyncExternalStore(approval.subscribe, approval.getSnapshot, approval.getSnapshot);
  useEffect(() => {
    resources.activate();
    recovery.activate();
    approval.activate();
    // Ingest synchronously with metadata publication so loading clears actions
    // before a delayed response or another user click can reuse old detail.
    const unsubscribeRecovery = recovery.subscribe(() => approval.setRecovery(recovery.getSnapshot()));
    approval.setRecovery(recovery.getSnapshot());
    void recovery.refresh();
    return () => { unsubscribeRecovery(); approval.release(); recovery.release(); resources.release(); };
  }, [resources, recovery, approval]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <CitationProvider>
        <PersonalChatWorkspaceProvider configuration={workspace} ownerKey={sessionId}>
        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col">
            <header className="flex min-h-16 shrink-0 items-center gap-3 border-b px-4 py-3">
              <ChatTitle sessionId={sessionId} title={title} onChanged={onChanged} />
              {selectedSkill && <span role="status" className="hidden max-w-48 truncate rounded-full border px-2 py-0.5 text-xs text-muted-foreground sm:inline">Skill: {selectedSkill.name} · revision {selectedSkill.revision}</span>}
              <RunRecoveryStatus view={recoveryView} onRefresh={recovery.refresh} />
              <WorkspaceFiles conversationId={sessionId} />
            </header>
            {error && <div role="alert" className="flex items-center gap-3 border-b bg-destructive/5 px-4 py-2 text-sm text-destructive"><p className="min-w-0 flex-1">{error}</p><button type="button" className="shrink-0 rounded-md px-2 py-0.5 text-xs hover:bg-destructive/10" onClick={() => setError(undefined)}>Dismiss</button></div>}
            <div className="min-h-0 flex-1">
              <Thread workspace={workspace} beforeComposer={<><RunFailed view={recoveryView} /><RunApprovalPanel view={approvalView} onRefresh={approval.refresh} onDecide={approval.decide} onResume={approval.resume} /></>} />
            </div>
            <PersonalChatPreviewPanel />
          </div>
          <SourcePreviewPanel />
        </div>
        </PersonalChatWorkspaceProvider>
      </CitationProvider>
    </AssistantRuntimeProvider>
  );
});

// A failed or stopped run leaves its question unfinished; offer to ask again.
function RunFailed({ view }: { view: RunRecoveryView }) {
  const aui = useAui();
  const running = useAuiState(state => state.thread.isRunning);
  const question = useAuiState(state => {
    const last = state.thread.messages.findLast(message => message.role === "user");
    return last?.content.map(part => part.type === "text" ? part.text : "").join("").trim() || null;
  });
  const status = view.phase === "ready" ? view.data?.status : undefined;
  if (running || !question || (status !== "failed" && status !== "cancelled")) return null;
  const retry = () => { aui.composer.setText(question); aui.composer.send(); };
  return <div role="alert" className="flex items-start gap-3 rounded-xl border border-destructive/25 bg-destructive/5 px-4 py-3 text-sm">
    <AlertCircleIcon className="mt-0.5 size-4 shrink-0 text-destructive" />
    <div className="min-w-0 flex-1">
      <p className="font-medium">This answer didn't finish</p>
      <p className="text-muted-foreground">{status === "failed" ? "The agent stopped before finishing the answer. Asking a narrower question often helps." : "The run was stopped. Review any partial output before trying again."}</p>
    </div>
    <button type="button" onClick={retry} className="flex shrink-0 items-center gap-1.5 rounded-lg border bg-background px-3 py-1.5 text-xs font-medium hover:bg-muted"><RotateCcwIcon className="size-3.5" />Try again</button>
  </div>;
}

const DEFAULT_TITLES = new Set(["New chat", "New conversation"]);

// Names a new chat after its first question, once, and reports whether it is empty.
function ChatTitle({ sessionId, title, onChanged }: { sessionId: string; title: string; onChanged?: AgentThreadProps["onChanged"] }) {
  const question = useAuiState(state => {
    const first = state.thread.messages.find(message => message.role === "user");
    return first ? first.content.map(part => part.type === "text" ? part.text : "").join(" ").replace(/\s+/g, " ").trim() : "";
  });
  const derived = question.length > 60 ? question.slice(0, 57).trimEnd() + "…" : question;
  const shown = DEFAULT_TITLES.has(title) && derived ? derived : title;
  useEffect(() => { onChanged?.({ empty: !question, renamed: false }); }, [question, onChanged]);
  useEffect(() => {
    if (!derived || !DEFAULT_TITLES.has(title)) return;
    const controller = new AbortController();
    void apiFetch(`/api/conversations/${encodeURIComponent(sessionId)}`, {
      method: "PATCH", credentials: "same-origin", signal: controller.signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: derived }),
    }).then(response => { if (response.ok) onChanged?.({ empty: false, renamed: true }); }).catch(() => undefined);
    return () => controller.abort();
  }, [derived, title, sessionId, onChanged]);
  return <h1 className="min-w-0 flex-1 truncate text-sm font-medium" title={shown}>{shown}</h1>;
}
