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
} from "@assistant-ui/react";
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
}

export const AgentThread = memo(function AgentThread({ sessionId, title = "New chat", selectedSkill, workspace, onSessionInvalid, onAuthInvalid }: AgentThreadProps) {
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
            <header className="flex h-12 shrink-0 items-center gap-3 border-b px-4 pl-12 md:pl-4">
              <h1 className="min-w-0 flex-1 truncate text-sm font-medium" title={title}>{title}</h1>
              {selectedSkill && <span role="status" className="hidden max-w-48 truncate rounded-full border px-2 py-0.5 text-xs text-muted-foreground sm:inline">Skill: {selectedSkill.name} · revision {selectedSkill.revision}</span>}
              <RunRecoveryStatus view={recoveryView} onRefresh={recovery.refresh} />
              <WorkspaceFiles conversationId={sessionId} />
            </header>
            {error && <div role="alert" className="flex items-center gap-3 border-b bg-destructive/5 px-4 py-2 text-sm text-destructive"><p className="min-w-0 flex-1">{error}</p><button type="button" className="shrink-0 rounded-md px-2 py-0.5 text-xs hover:bg-destructive/10" onClick={() => setError(undefined)}>Dismiss</button></div>}
            <div className="min-h-0 flex-1">
              <Thread workspace={workspace} beforeComposer={<RunApprovalPanel view={approvalView} onRefresh={approval.refresh} onDecide={approval.decide} onResume={approval.resume} />} />
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
