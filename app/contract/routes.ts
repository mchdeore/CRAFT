import type * as T from "./types";
import type { RunAgentInput, AGUIEvent } from "@ag-ui/core";
export interface Routes {
  "GET /api/approvals": { query: { status?: T.ApprovalStatus }; response: T.Approval[] };
  "GET /api/approvals/:id": { response: T.ApprovalDetail };
  "POST /api/approvals/:id/decision": { body: T.Decision; response: T.Approval };
  "GET /api/notebook-grants": { query: { conversationId?: T.Id }; response: T.NotebookGrant[] };
  "DELETE /api/notebook-grants/:id": { response: void };
  "GET /api/tools": { response: T.ToolInfo[] };
  "GET /api/tools/capabilities": { response: T.CapabilityCatalog };
  "GET /api/library/documents": { query: T.DocumentList; response: T.DocumentCatalog };
  "POST /api/agent/runs": { body: RunAgentInput & { skill?: T.SkillVersionRef | null }; stream: AGUIEvent };
  "GET /api/agent/runs/:run/events": { query: { after?: number }; stream: AGUIEvent };
  "POST /api/agent/runs/:run/cancel": { response: void };
  "POST /api/cube/candidates": { body: T.PrepareValues; response: T.Candidates };
  "POST /api/cube/values": { body: T.SubmitValues; response: T.ValueReceipt };
  "POST /api/library/citations/check": { body: T.CitationCheck; response: T.CitationStatus };
  "GET /api/library/documents/:item/compare": { query: { fromVersion: number; toVersion: number }; response: T.VersionDiff };
  "GET /health/live": { response: T.Health };
  "GET /health/ready": { response: T.Health };
  "GET /health/queue": { response: T.QueueHealth };
  "GET /api/jobs": { query: T.JobPageRequest; response: T.JobPage };
  "GET /api/jobs/:jobId": { response: T.Job };
  "POST /api/jobs/:jobId/retry": { response: T.Job };
  "POST /api/jobs/:jobId/cancel": { response: T.Job };
  "POST /api/library/search": { body: T.LibrarySearch; response: T.LibraryHits };
  "GET /api/library/documents/:item": { query: { version?: number }; response: T.LibraryDocument };
  "GET /api/library/documents/:item/chunks": { query: { version: number; fromBlock: number; toBlock: number }; response: T.ChunkPage };
  "POST /api/library/explore": { body: T.ExploreInput; response: T.ChunkPage };
  "GET /api/library/documents/:item/preview": { query: { version: number; locator?: string }; response: Blob };
  "GET /api/library/documents/:item/download": { query: { version: number; locator?: string }; response: Blob };
  "POST /api/library/documents/:item/revisions": { body: Blob; query: { version: number }; response: T.Revision };
  "GET /api/library/documents/:item/revisions": { response: T.Revision[] };
  "GET /api/library/documents/:item/revisions/:revision": { response: T.Revision };
  "GET /api/library/documents/:item/revisions/:revision/content": { response: Blob };
  "GET /api/auth/login": { query: { returnTo?: string }; response: void }; // 302 to the identity provider
  "GET /api/auth/callback": { query: { code?: string; state?: string; error?: string }; response: void }; // the provider's redirect back; 302 onward
  "POST /api/auth/logout": { response: T.SignedOut }; // then send the browser to logoutUrl when it is set
  "POST /api/session": { body: T.SignIn; response: T.Me }; // tests only (CRAFT_LOCAL_PASSWORD_SIGNIN)
  "DELETE /api/session": { response: void };
  "GET /api/me": { response: T.Me };
  "PATCH /api/me": { body: T.MeChange; response: T.Me };
  "POST /api/me/workspace-tokens": { body: T.NewWorkspaceToken; response: T.IssuedWorkspaceToken };
  "GET /api/me/workspace-tokens": { response: T.WorkspaceToken[] };
  "DELETE /api/me/workspace-tokens/:id": { response: void };
  "GET /api/workspace": { response: T.Workspace };
  "POST /api/workspace/start": { response: T.Workspace };
  "POST /api/workspace/stop": { response: T.Workspace };
  "POST /api/workspace/run": { body: T.RunCode; response: T.CodeResult };
  "PUT /api/workspace/files": { body: T.WriteFile; response: T.SavedFile };
  "POST /api/workspace/notebooks/edit": { body: T.EditNotebook; response: T.EditedNotebook };
  "POST /api/workspace/charts": { body: T.SaveChart; response: T.SavedChart };
  "POST /api/workspace/notebooks/run": { body: T.GrantedRun; response: T.CodeResult };
  "POST /api/workspace/notebooks/edit-granted": { body: T.EditNotebook; response: T.EditedNotebook };
  "POST /api/workspace/notebooks/runs": { body: T.RunNotebook; response: T.NotebookRunQueued };
  "GET /api/cube/missions": { query: {query?: string; limit?: number}; response: T.Mission[] };
  "GET /api/cube/parameters": { response: T.Parameter[] };
  "POST /api/cube/find": { body: T.FindMissions; response: T.FoundMissions };
  "GET /api/cube/missions/:key": { query: {silo?: string; stage?: string; at?: string; knownAt?: string}; response: T.MissionState };
  "POST /api/cube/compare": { body: T.CompareMissions; response: T.Comparison };
  "GET /api/cube/values/:id": { response: T.ValueEvidence };
  "POST /api/summarize": { body: T.SelectedSource; response: T.SummaryResult };
  "POST /api/summarize/upload": { body: Blob; response: T.SummaryResult };
  "POST /api/cube/similar": { body: T.SimilarMissions; response: T.SimilarPage };
  "POST /api/cube/similar/explain": { body: T.ExplainSimilarity; response: T.SimilarityExplanation };

  "GET /api/conversations": { response: T.ConversationSummary[] };
  "POST /api/conversations": { body: T.NewConversation; response: T.ConversationSummary };
  "GET /api/conversations/:id": { response: T.Conversation };
  "GET /api/conversations/:id/run": { response: T.RunRecovery };
  "PATCH /api/conversations/:id": { body: T.ConversationUpdate; response: T.ConversationSummary };
  "GET /api/conversations/:id/files": { response: T.WorkspaceFile[] };
  "POST /api/conversations/:id/files": { body: Blob; query: { classification?: T.Classification }; response: T.WorkspaceFile };
  "GET /api/conversations/:id/files/:fileId/content": { response: Blob };
  "DELETE /api/conversations/:id/files/:fileId": { response: void };

  "PUT /api/notes": { body: T.WriteNote; response: T.Note };
  "GET /api/notes": { query: { conversationId?: string }; response: T.NoteSummary[] };
  "GET /api/notes/:id": { response: T.Note };
  "DELETE /api/notes/:id": { response: void };
  "PUT /api/skills/:id": { body: Omit<T.SaveSkill, "id">; response: T.Skill };
  "GET /api/skills": { query: T.SkillList; response: T.SkillCatalog };
  "GET /api/skills/:id": { response: T.Skill };
  "GET /api/skills/:id/versions": { query: { revision: number }; response: T.Skill };
  "POST /api/agent/ask": { body: T.AskAgent; response: T.AskAgentResult };
  "POST /api/tools/chart": { body: T.ChartInput; response: T.ChartResult };
  "POST /api/tools/calculation-check": { body: T.CalculationInput; response: T.CalculationResult };
}
