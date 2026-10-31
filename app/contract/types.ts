import type { Message as AgUiMessage } from "@ag-ui/core";

export type Id = string;
export type Role = "member" | "admin";
export type Language = "en" | "fr";
/** `language` is the reply language; change it with PATCH /api/me. */
export interface Me { id: Id; username: string; displayName: string; roles: Role[]; language: Language; email?: string | null }
export interface MeChange { language: Language }
/** Browser sign-in goes to GET /api/auth/login?returnTo=…; failures come back to /?signIn=failed or /?signIn=no-access. */
export interface SignedOut { logoutUrl: string | null }
/** `workspace` signs in at /api from owned Python; `mcp` signs in at /mcp from an outside MCP client. */
export type TokenKind = "workspace" | "mcp";
/** `hours`: 1 to 168 for a workspace token (default 12), 1 to 720 for an MCP token (default 168). `kind` defaults to `workspace`. */
export interface NewWorkspaceToken { label: string; hours?: number | null; kind?: TokenKind }
export interface WorkspaceToken { id: Id; kind: TokenKind; label: string; createdAt: string; expiresAt: string; lastUsedAt: string | null; revokedAt: string | null }
export interface IssuedWorkspaceToken extends WorkspaceToken { token: string }
export interface Workspace { state: "stopped" | "starting" | "ready"; url: string | null }
/** `notebook`: the notebook this code works on. Approving the run also covers later runs in this conversation (`run_in_granted_notebook`): all code runs in the one analysis kernel. */
export interface RunCode { code: string; notebook?: string | null }
export interface GrantedRun { notebook: string; code: string }
export interface KernelImage { mediaType: "image/png" | "image/jpeg"; dataBase64: string }
export interface CodeResult { status: "ok" | "error"; outputs: string[]; error: string | null; truncated: boolean; traceback?: string[]; images?: KernelImage[] }
export interface NotebookGrant { id: Id; conversationId: Id; path: string; approvalId: Id; createdAt: string }
export interface WriteFile { path: string; content: string }
export interface SavedFile { path: string; size: number }
export interface SaveChart { name: string; spec: Record<string, unknown>; title?: string | null }
export interface SavedChart { svg: string; spec: string; csv: string }
export interface EditNotebook { path: string; action: "insert" | "replace" | "delete"; index: number; source?: string; cellType?: "code" | "markdown" }
export interface EditedNotebook { path: string; cells: number }
export interface RunNotebook { path: string }
/** The run is a background job (`GET /api/jobs/:jobId`); `output` is the executed copy it writes beside the notebook. */
export interface NotebookRunQueued { jobId: string; notebook: string; output: string }
export interface Mission { key: string; title: string; currentStage: string; aliases: string[]; simulated: boolean }
export interface ListMissions { query?: string; limit?: number }
export interface Health { status: "ok" | "unavailable" }
export interface FieldError { path: string; message: string }
/** Every `code` the server sends, with its HTTP status. A chat stream's `RUN_ERROR` uses these codes, plus `run_cancelled` and `run_interrupted`. */
export type ErrorCode =
  | "invalid_input"          // 400: `fields` lists each problem
  | "not_understood"         // 400: the calculation service couldn't read the expression; `fields` carries its reply
  | "not_signed_in"          // 401
  | "sign_in_failed"         // 401: browser sign-in did not complete
  | "forbidden"              // 403
  | "not_found"              // 404
  | "conflict"               // 409
  | "approval_required"      // 409: the call needs approval first
  | "too_large"              // 413
  | "rate_limited"           // 429
  | "run_limit"              // 429: a run used up its tool calls
  | "budget_exhausted"       // 429: the model budget is spent
  | "internal_error"         // 500
  | "external_unavailable"   // 502: the calculation service is unavailable
  | "not_configured"         // 503: the calculation service is not configured
  | "workspace_unavailable"  // 503: the Hub or the workspace failed
  | "run_timeout";           // 504: code ran past its limit and was interrupted
export interface ApiError { code: ErrorCode; message: string; requestId: string; fields?: FieldError[] }
export type Classification = "unclassified" | "protected-a" | "protected-b";
export interface CitationHandle { item: string; version: number; block: number; locator: string; quoteSha256: string; simulated: boolean }
export interface Tags { mission: string | null; silo: string | null; stage: string | null }
export interface LibrarySearch { query: string; missions?: string[]; silos?: string[]; stages?: string[]; kinds?: ("chunk" | "document")[]; limit?: number }
export interface ChunkText { handle: CitationHandle; headingPath: string; text: string; score?: number }
export interface LibraryHit { kind: "chunk" | "document"; handle: CitationHandle; title: string; headingPath: string; text: string; score: number; tags: Tags; classification: Classification; before: ChunkText | null; after: ChunkText | null }
export interface LibraryHits { hits: LibraryHit[]; truncated: boolean }
export interface DocumentRef { item: string; version?: number }
export interface DocumentList { limit?: number }
export interface CatalogDocument { item: string; title: string; version: number; classification: Classification; simulated: boolean }
export interface DocumentCatalog { documents: CatalogDocument[]; truncated: boolean }
export type JobState = "queued" | "scheduled" | "started" | "stopping" | "finished" | "failed" | "canceled" | "stopped";
export interface JobPageRequest { status?: JobState | null; offset?: number; limit?: number }
/** How far a running job got; `note` is a short fixed phrase. */
export interface JobProgress { done: number; total: number; note: string | null }
/** `progress` comes only from GET /api/jobs/:jobId, once the job has reported any. */
export interface Job { jobId: string; kind: string; queue: string; status: JobState; attempts: number; requestedBy: string | null; enqueuedAt: string | null; startedAt: string | null; finishedAt: string | null; error: string | null; progress?: JobProgress | null }
export interface JobPage { jobs: Job[]; nextOffset: number | null }
/** `task`: up to 60 tool calls and 15 minutes, one running at a time per person; it notifies when it ends or waits for approval. */
export type RunMode = "chat" | "task";
export type NotificationKind = "job_done" | "job_failed" | "run_done" | "run_waiting";
export interface NotificationPageRequest { unread?: boolean; limit?: number }
/** `refKind` and `refId` name a job (GET /api/jobs/:jobId) or a run. Clients poll; there is no push. */
export interface Notification { id: Id; kind: NotificationKind; refKind: "job" | "run"; refId: string; createdAt: string; readAt: string | null }
export interface LibraryDocument { item: string; version: number; title: string; path: string; tags: Tags; summary: string; summaryKind: "extract" | "generated"; outline: { block: number; headingPath: string }[]; versions: { version: number; sha256: string; indexedAt: string }[]; simulated: boolean; classification: Classification; deleted: boolean }
export interface ChunkRange { item: string; version: number; fromBlock: number; toBlock: number }
export interface ExploreInput { handle: CitationHandle; before?: number; after?: number; query?: string }
export interface ChunkPage { chunks: ChunkText[]; truncated: boolean }
export interface RelationsRequest { item?: string | null; mission?: string | null }
/** One graph shape for library relations and workspace graphs. Library node IDs: an item, `item@version`, `value:<id>`, `facet:<id>`, `mission:<key>`. */
export interface GraphNode { id: string; kind: "file" | "document" | "version" | "value" | "facet" | "mission"; label: string; linked: boolean; missing: boolean; path?: string; item?: string; group?: string }
export interface GraphEdge { id: string; from: string; to: string; relation: string; note?: string }
export interface GraphGroup { id: string; label: string; parent?: string }
export interface Graph { nodes: GraphNode[]; edges: GraphEdge[]; groups: GraphGroup[] }
export type ValueKind = "exact" | "range" | "unknown" | "not_applicable";
export interface Parameter {
  key: string; label: string; unit: string; min: number | null; max: number | null; whole: boolean;
  units: { unit: string; multiplier: string; offset: string }[];
}
export interface Condition {
  parameter: string; op: "eq" | "ge" | "le" | "between"; value: string; high?: string; unit: string;
}
export interface FindMissions {
  conditions: Condition[]; silo?: string; stage?: string;        // stage "current" = each mission's current stage
  at?: string; knownAt?: string; limit?: number;
}
export interface CubeValue {
  id: Id; mission: string; silo: string; stage: string | null; element: string | null; parameter: string;
  kind: ValueKind; low: number | null; high: number | null; unit: string;
  sourceNumber: string | null; sourceUnit: string | null;
  validFrom: string; recordedAt: string; citation: CitationHandle;
  withdrawn: boolean;                  // true: shown as unknown, source withdrawn
  approvalId: Id | null; simulated: boolean;
}
export interface FoundMissions {
  missions: { mission: Mission; matched: CubeValue[] }[]; at: string; knownAt: string; truncated: boolean;
}
export interface MissionRef { key: string; silo?: string; stage?: string; at?: string; knownAt?: string }
export interface ValueRef { id: Id }
export interface MissionState {
  mission: Mission; values: CubeValue[]; gaps: string[];         // parameter keys without a value
  at: string; knownAt: string;
}
export interface CompareMissions {
  keys: string[]; parameters: string[]; silo?: string; stage?: string; at?: string; knownAt?: string;
}
export interface Comparison {
  parameters: string[]; rows: { mission: Mission; values: (CubeValue | null)[] }[];
  at: string; knownAt: string;
}
export interface ValueEvidence {
  value: CubeValue; quote: string; conversion: string;
  citationStatus: "current" | "changed" | "newer_version" | "withdrawn";
}

export interface SelectedSource { item: string; version: number; sha256: string }
export interface SummarySource { kind: "document" | "upload"; fileName: string; version: string; item: string | null; providerVersion: number | null; simulated: boolean }
export interface SummaryCitation { handle: CitationHandle | null; block: number; blockVersion: string; locator: string; quote: string; headingPath: string }
export interface SummarySentence { text: string; citations: SummaryCitation[] }
export interface FilingSuggestion { value: string | null; confidence: number; evidence: SummaryCitation[]; path: string | null }
export interface Confidence { score: number; level: "high" | "medium" | "low"; reason: string }
export interface SummaryResult { source: SummarySource; sentences: SummarySentence[]; mission: FilingSuggestion; silo: FilingSuggestion; stage: FilingSuggestion; confidence: Confidence; status: "supported" | "insufficient_evidence" }

/** CRAFT's own full copy of a document; it always names the source version it came from and is never written back. */
export interface Revision { item: string; revision: number; sourceVersion: number; fileName: string; mediaType: string; sizeBytes: number; contentSha256: string; authorId: Id; createdAt: string; simulated: boolean; classification: string }
export interface CitationCheck { handle: CitationHandle }
export interface CitationStatus { status: "current" | "changed" | "newer_version" | "withdrawn"; latestVersion: number }
export interface CompareVersions { item: string; fromVersion: number; toVersion: number }
export interface VersionDiff { blocks: {change: "added" | "removed" | "changed" | "moved"; fromBlock: number | null; toBlock: number | null}[]; truncated: boolean }

export interface PrepareValues { handle: CitationHandle; mission?: string; silo?: string; stage?: string; validFrom?: string }
export interface Candidate { row: number; header: string; parameter: string | null; element: string | null; sourceNumber: string; sourceUnit: string | null; kind: ValueKind; low: number | null; high: number | null; citation: CitationHandle; problem: string | null }
export interface Candidates { mission: string | null; silo: string | null; stage: string | null; validFrom: string | null; candidates: Candidate[]; problems: string[] }
export interface SubmitValues { mission: string; silo: string; stage?: string | null; validFrom: string; candidates: Candidate[] }
export interface ValueReceipt { approvalId: Id; valueIds: Id[] }

export type Facet = "purpose" | "design" | "operations" | "program" | "cost_and_risk";
export interface SimilarMissions { key: string; facets?: Facet[]; limit?: number }
export interface SimilarMission {
  mission: Mission; score: number | null; comparable: boolean;
  facetScores: Partial<Record<Facet, number | null>>;
}
export interface SimilarPage { missions: SimilarMission[]; facets: Facet[] }
export interface ExplainSimilarity { key: string; other: string; facets?: Facet[] }
export interface FacetExplanation {
  facet: Facet; score: number | null; text: string | null; otherText: string | null;
  sources: string[]; otherSources: string[];
}
export interface SimilarityExplanation { facets: FacetExplanation[] }
export interface Capability { name: string; description: string }
export interface CapabilityCatalog { tools: Capability[]; skills: string[]; skillsStatus: "not_installed" | "installed" }
export interface ToolInfo {
  name: string; operation: string; description: string;
  effects: "NONE" | "DATA" | "DOCUMENT" | "EXTERNAL" | "FINAL";
  needsApproval: boolean; route: string;
  inputSchema: Record<string, unknown>;
  outputSchema: Record<string, unknown> | null;
  availableIn: ("api" | "mcp" | "chat" | "read_only_query")[];
}

export interface Citation {
  handle: CitationHandle; title: string; headingPath: string;
  classification: Classification; quote: string;
}
export interface AnswerMeta { messageId: string; citations: Citation[]; confidence: Confidence }
/** `craft.originals`: the untranslated quotes behind a reply in another language, shown collapsed. */
export interface Original { handle: CitationHandle; language: Language; text: string }
export interface AnswerOriginals { messageId: string; items: Original[] }

export interface NewConversation { title?: string; groupName?: string | null }
export interface ConversationUpdate { title?: string; groupName?: string | null; archived?: boolean }
export interface ConversationSummary { id: Id; title: string; groupName: string | null; updatedAt: string; archived: boolean; pendingApprovals: number }
export interface Conversation extends ConversationSummary { messages: AgUiMessage[] }
export interface RunRecoveryRef { id: Id }
export interface RunRecovery {
  status: "none" | "running" | "paused" | "finished" | "failed" | "cancelled";
  runId: Id | null; startedAt: string | null; endedAt: string | null;
  approvalId: Id | null; approvalStatus: ApprovalStatus | null; canResume: boolean;
  /** Events in the run's newest stream; re-attach with GET /api/agent/runs/:run/events?after=N. */
  lastEvent: number | null;
}
export interface RetrievalStep { tool: string; operation: string; arguments: Record<string, unknown>; at: string; toolCallId: string }
export type RetrievalPath = RetrievalStep[];
export interface RetrievalRecord { messageId: Id; runId: Id; model: string; instructionsVersion: string; path: RetrievalPath; skills?: SkillVersionRef[]; replyLanguage?: Language; originals?: number }
export interface SkillUsage { runId: Id; skills: SkillVersionRef[] }
export type WorkspaceFileState = "uploading" | "processing" | "ready" | "failed" | "export-pending" | "exported";
export interface WorkspaceFile { id: Id; conversationId: Id; name: string; mediaType: string; sizeBytes: number; origin: "upload" | "agent"; state: WorkspaceFileState; simulated: boolean; classification: Classification; createdAt: string; statusDetail?: string; exportApprovalId?: Id }

export interface WriteNote { title: string; body: string; conversationId?: Id | null }
export interface NoteList { conversationId?: Id | null }
export interface NoteRef { id: Id }
export interface NoteSummary { id: Id; title: string; conversationId: Id | null; sizeBytes: number; createdAt: string; updatedAt: string }
export interface Note extends NoteSummary { body: string }
export interface SkillRef { id: Id }
export interface SkillVersionRef extends SkillRef { revision: number }
export interface SkillList { after?: Id | null; limit?: number }
export interface SaveSkill extends SkillRef { expectedRevision: number; mutationId: Id; name: string; description: string; instructions: string; exampleInputs: string[]; toolNames: string[] }
export interface SkillSummary { id: Id; revision: number; name: string; description: string; toolNames: string[]; unavailableTools: string[]; createdAt: string; updatedAt: string }
export interface Skill extends SkillSummary { instructions: string; exampleInputs: string[] }
export interface SkillCatalog { items: SkillSummary[]; nextCursor: Id | null }
export interface AskAgent { question: string; conversationId?: Id | null }
export interface AskAgentResult { answer: string; citations: Citation[]; confidence: Confidence; runId: Id }
/** The question runs as an `agent.agent_run` job (`GET /api/jobs/:jobId`); read its answer with `GET /api/agent/answers/:jobId`. */
export interface AskAgentQueued { jobId: string; conversationId: Id }
/** `result` is set once `status` is `answered`; a failed run returns its error instead. Answers are kept for a day. */
export interface Answer { status: "pending" | "answered"; result: AskAgentResult | null }

export interface ChartInput { spec: Record<string, unknown>; format?: "svg" | "png"; title?: string | null; sources?: CitationHandle[] }
/** In a conversation the chart is kept as four files; outside one the image comes inline. */
export interface ChartFiles { svg: WorkspaceFile; png: WorkspaceFile; spec: WorkspaceFile; csv: WorkspaceFile }
export interface ChartResult { title: string; format: "svg" | "png"; mediaType: string; contentBase64: string | null; file: WorkspaceFile | null; files: ChartFiles | null; citations: CitationHandle[]; simulated: boolean }
export interface CalculationInput { expression: string; craftResult?: string | number | null; unit?: string | null }
export interface CalculationResult { expression: string; wolframResult: string; craftResult: string | number | null; agrees: boolean; checkedAt: string }

export type ApprovalStatus = "pending" | "approved" | "rejected" | "executed" | "failed";
export interface Approval { id: Id; conversationId: Id; runId: Id; tool: string; summary: string; status: ApprovalStatus; requestedAt: string }
export interface ApprovalEvent { kind: "requested" | "approved" | "rejected" | "executed" | "failed"; actorId: Id | null; at: string; note?: string | null }
export interface ApprovalDetail extends Approval { arguments: Record<string, unknown>; evidence: Record<string, unknown>[]; reasoning: string; retrievalPath: Record<string, unknown>[]; events: ApprovalEvent[] }
export interface Decision { approve: boolean; note?: string | null }
