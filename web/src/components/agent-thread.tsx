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
  selectedSkill?: SkillSelection;
  workspace?: WorkspaceConfiguration;
  onSessionInvalid: () => void | Promise<void>;
  onAuthInvalid: () => void;
}

export const AgentThread = memo(function AgentThread({ sessionId, selectedSkill, workspace, onSessionInvalid, onAuthInvalid }: AgentThreadProps) {
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
      {error && <p role="alert" className="px-4 py-2 text-sm text-destructive">{error}</p>}
      <CitationProvider>
        <PersonalChatWorkspaceProvider configuration={workspace} ownerKey={sessionId}>
        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 px-3 pt-2">
              <RunRecoveryStatus view={recoveryView} onRefresh={recovery.refresh} />
              <WorkspaceFiles conversationId={sessionId} />
            </div>
            <RunApprovalPanel view={approvalView} onRefresh={approval.refresh} onDecide={approval.decide} onResume={approval.resume} />
            <div className="min-h-0 flex-1">
              <Thread workspace={workspace} />
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
