import type * as T from "./types";
import type { RunAgentInput, AGUIEvent } from "@ag-ui/core";
export interface Routes {
  "GET /api/approvals": { query: { status?: T.ApprovalStatus }; response: T.Approval[] }; // example: approvals.json
  "GET /api/approvals/:id": { response: T.ApprovalDetail }; // example: approval-detail.json
  "POST /api/approvals/:id/decision": { body: T.Decision; response: T.Approval }; // example: approval.json
  "GET /api/notebook-grants": { query: { conversationId?: T.Id }; response: T.NotebookGrant[] }; // example: notebook-grants.json
  "DELETE /api/notebook-grants/:id": { response: void };
  "GET /api/tools": { response: T.ToolInfo[] }; // example: tools.json
  "GET /api/tools/capabilities": { response: T.CapabilityCatalog }; // example: capabilities.json
  "GET /api/library/documents": { query: T.DocumentList; response: T.DocumentCatalog }; // example: document-catalog.json
  "POST /api/agent/runs": { body: RunAgentInput & { skill?: T.SkillVersionRef | null; mode?: T.RunMode }; stream: AGUIEvent };
  "GET /api/agent/runs/:run/events": { query: { after?: number }; stream: AGUIEvent };
  "POST /api/agent/runs/:run/cancel": { response: void };
  "POST /api/cube/candidates": { body: T.PrepareValues; response: T.Candidates }; // example: candidates.json
  "POST /api/cube/values": { body: T.SubmitValues; response: T.ValueReceipt }; // example: value-receipt.json
  "POST /api/library/citations/check": { body: T.CitationCheck; response: T.CitationStatus }; // example: citation-status.json
  "GET /api/library/documents/:item/compare": { query: { fromVersion: number; toVersion: number }; response: T.VersionDiff }; // example: version-diff.json
  "GET /health/live": { response: T.Health }; // example: health-live.json
  "GET /health/ready": { response: T.Health }; // example: health-ready.json
  "GET /health/queue": { response: T.QueueHealth }; // example: queue-health.json
  "GET /api/jobs": { query: T.JobPageRequest; response: T.JobPage }; // example: job-page.json
  "GET /api/jobs/:jobId": { response: T.Job }; // example: job.json
  "POST /api/jobs/:jobId/cancel": { response: T.Job }; // example: job.json
  "GET /api/notifications": { query: T.NotificationPageRequest; response: T.Notification[] }; // example: notifications.json
  "POST /api/notifications/:id/read": { response: T.Notification }; // example: notification.json
  "POST /api/library/search": { body: T.LibrarySearch; response: T.LibraryHits }; // example: library-search.json
  "GET /api/library/documents/:item": { query: { version?: number }; response: T.LibraryDocument }; // example: library-document.json
  "GET /api/library/documents/:item/chunks": { query: { version: number; fromBlock: number; toBlock: number }; response: T.ChunkPage }; // example: chunks.json
  "POST /api/library/explore": { body: T.ExploreInput; response: T.ChunkPage }; // example: explore.json
  "GET /api/library/relations": { query: T.RelationsRequest; response: T.Graph }; // example: relations.json
  "GET /api/library/documents/:item/preview": { query: { version: number; locator?: string }; response: Blob };
  "GET /api/library/documents/:item/download": { query: { version: number; locator?: string }; response: Blob };
  "POST /api/library/documents/:item/revisions": { body: Blob; query: { version: number }; response: T.Revision }; // example: revision.json
  "GET /api/library/documents/:item/revisions": { response: T.Revision[] }; // example: revisions.json
  "GET /api/library/documents/:item/revisions/:revision": { response: T.Revision }; // example: revision.json
  "GET /api/library/documents/:item/revisions/:revision/content": { response: Blob };
  "GET /api/auth/login": { query: { returnTo?: string }; response: void }; // 302 to the identity provider
  "GET /api/auth/callback": { query: { code?: string; state?: string; error?: string }; response: void }; // the provider's redirect back; 302 onward
  "POST /api/auth/logout": { response: T.SignedOut }; // then send the browser to logoutUrl when it is set; example: signed-out.json
  "POST /api/session": { body: T.SignIn; response: T.Me }; // tests only (CRAFT_LOCAL_PASSWORD_SIGNIN); example: session.json
  "DELETE /api/session": { response: void };
  "GET /api/me": { response: T.Me }; // example: me.json
  "PATCH /api/me": { body: T.MeChange; response: T.Me }; // example: me.json
  "POST /api/me/tokens": { body: T.NewWorkspaceToken; response: T.IssuedWorkspaceToken }; // example: mcp-token.json
  "GET /api/me/tokens": { response: T.WorkspaceToken[] }; // example: tokens.json
  "DELETE /api/me/tokens/:id": { response: void };
  // The earlier names of the three /api/me/tokens routes.
  "POST /api/me/workspace-tokens": { body: T.NewWorkspaceToken; response: T.IssuedWorkspaceToken }; // example: workspace-token.json
  "GET /api/me/workspace-tokens": { response: T.WorkspaceToken[] }; // example: workspace-tokens.json
  "DELETE /api/me/workspace-tokens/:id": { response: void };
  "POST /api/workspace-tokens/hub": { response: T.IssuedWorkspaceToken }; // example: workspace-token.json
  "GET /api/workspace": { response: T.Workspace }; // example: workspace.json
  "POST /api/workspace/start": { response: T.Workspace }; // example: workspace.json
  "POST /api/workspace/stop": { response: T.Workspace }; // example: workspace-stopped.json
  "POST /api/workspace/run": { body: T.RunCode; response: T.CodeResult }; // example: code-result.json
  "PUT /api/workspace/files": { body: T.WriteFile; response: T.SavedFile }; // example: saved-file.json
  "POST /api/workspace/notebooks/edit": { body: T.EditNotebook; response: T.EditedNotebook }; // example: edited-notebook.json
  "POST /api/workspace/charts": { body: T.SaveChart; response: T.SavedChart }; // example: saved-chart.json
  "POST /api/workspace/notebooks/run": { body: T.GrantedRun; response: T.CodeResult }; // example: code-result.json
  "POST /api/workspace/notebooks/edit-granted": { body: T.EditNotebook; response: T.EditedNotebook }; // example: edited-notebook.json
  "POST /api/workspace/notebooks/runs": { body: T.RunNotebook; response: T.NotebookRunQueued }; // example: notebook-run.json
  "GET /api/cube/missions": { query: {query?: string; limit?: number}; response: T.Mission[] }; // example: missions.json
  "GET /api/cube/parameters": { response: T.Parameter[] }; // example: parameters.json
  "POST /api/cube/find": { body: T.FindMissions; response: T.FoundMissions }; // example: found-missions.json
  "GET /api/cube/missions/:key": { query: {silo?: string; stage?: string; at?: string; knownAt?: string}; response: T.MissionState }; // example: mission-state.json
  "POST /api/cube/compare": { body: T.CompareMissions; response: T.Comparison }; // example: comparison.json
  "GET /api/cube/values/:id": { response: T.ValueEvidence }; // example: value-evidence.json
  "POST /api/summarize": { body: T.SelectedSource; response: T.SummaryResult }; // example: summarize-selected.json
  "POST /api/summarize/upload": { body: Blob; response: T.SummaryResult }; // example: summarize-uploaded.json
  "POST /api/cube/similar": { body: T.SimilarMissions; response: T.SimilarPage }; // example: similar-missions.json
  "POST /api/cube/similar/explain": { body: T.ExplainSimilarity; response: T.SimilarityExplanation }; // example: similarity-explanation.json

  "GET /api/conversations": { response: T.ConversationSummary[] }; // example: conversations.json
  "POST /api/conversations": { body: T.NewConversation; response: T.ConversationSummary }; // example: conversation-summary.json
  "GET /api/conversations/:id": { response: T.Conversation }; // example: conversation.json
  "GET /api/conversations/:id/run": { response: T.RunRecovery }; // example: run-recovery.json
  "PATCH /api/conversations/:id": { body: T.ConversationUpdate; response: T.ConversationSummary }; // example: conversation-summary.json
  "GET /api/conversations/:id/files": { response: T.WorkspaceFile[] }; // example: workspace-files.json
  "POST /api/conversations/:id/files": { body: Blob; query: { classification?: T.Classification }; response: T.WorkspaceFile }; // example: workspace-file.json
  "GET /api/conversations/:id/files/:fileId/content": { response: Blob };
  "DELETE /api/conversations/:id/files/:fileId": { response: void };

  "PUT /api/notes": { body: T.WriteNote; response: T.Note }; // example: note.json
  "GET /api/notes": { query: { conversationId?: string }; response: T.NoteSummary[] }; // example: notes.json
  "GET /api/notes/:id": { response: T.Note }; // example: note.json
  "DELETE /api/notes/:id": { response: void };
  "PUT /api/skills/:id": { body: Omit<T.SaveSkill, "id">; response: T.Skill }; // example: skill.json
  "GET /api/skills": { query: T.SkillList; response: T.SkillCatalog }; // example: skills.json
  "GET /api/skills/:id": { response: T.Skill }; // example: skill.json
  "GET /api/skills/:id/versions": { query: { revision: number }; response: T.Skill }; // example: skill.json
  "POST /api/agent/ask": { body: T.AskAgent; response: T.AskAgentQueued }; // example: ask-agent.json
  "GET /api/agent/answers/:jobId": { response: T.Answer }; // example: answer.json
  "POST /api/tools/chart": { body: T.ChartInput; response: T.ChartResult }; // example: chart.json
  "POST /api/tools/calculation-check": { body: T.CalculationInput; response: T.CalculationResult }; // example: calculation-check.json
}
