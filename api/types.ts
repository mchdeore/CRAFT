import type { Message as AgUiMessage } from "@ag-ui/core";

export type Id = string;
export type Role = "member" | "admin";
export interface SignIn { username: string; password: string }
export interface Me { id: Id; username: string; displayName: string; roles: Role[] }
export interface Mission { key: string; title: string; currentStage: string; aliases: string[]; simulated: boolean }
export interface ListMissions { query?: string; limit?: number }
export interface Health { status: "ok" | "unavailable" }
export interface FieldError { path: string; message: string }
export interface ApiError { code: string; message: string; requestId: string; fields?: FieldError[] }
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
export interface LibraryDocument { item: string; version: number; title: string; path: string; tags: Tags; summary: string; summaryKind: "extract" | "generated"; outline: { block: number; headingPath: string }[]; versions: { version: number; sha256: string; indexedAt: string }[]; simulated: boolean; classification: Classification; deleted: boolean }
export interface ChunkRange { item: string; version: number; fromBlock: number; toBlock: number }
export interface ExploreInput { handle: CitationHandle; before?: number; after?: number; query?: string }
export interface ChunkPage { chunks: ChunkText[]; truncated: boolean }
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

export interface CitationCheck { handle: CitationHandle }
export interface CitationStatus { status: "current" | "changed" | "newer_version" | "withdrawn"; latestVersion: number }
export interface CompareVersions { item: string; fromVersion: number; toVersion: number }
export interface VersionDiff { blocks: {change: "added" | "removed" | "changed" | "moved"; fromBlock: number | null; toBlock: number | null}[]; truncated: boolean }

export interface PrepareValues { handle: CitationHandle; mission?: string; silo?: string; stage?: string; validFrom?: string }
export interface Candidate { row: number; header: string; parameter: string | null; element: string | null; sourceNumber: string; sourceUnit: string | null; kind: ValueKind; low: number | null; high: number | null; citation: CitationHandle; problem: string | null }
export interface Candidates { mission: string | null; silo: string | null; stage: string | null; validFrom: string | null; candidates: Candidate[]; problems: string[] }
export interface SubmitValues { mission: string; silo: string; stage: string | null; validFrom: string; candidates: Candidate[] }
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
export interface CapabilityCatalog { tools: Capability[]; skills: string[]; skillsStatus: "not_installed" }
export interface ToolInfo {
  name: string; operation: string; description: string;
  effects: "NONE" | "DATA" | "DOCUMENT" | "EXTERNAL" | "FINAL";
  needsApproval: boolean; route: string;
}

export interface Citation {
  handle: CitationHandle; title: string; headingPath: string;
  classification: Classification; quote: string;
}
export interface AnswerMeta { messageId: string; citations: Citation[]; confidence: Confidence }

export interface NewConversation { title?: string }
export interface ConversationUpdate { title?: string; archived?: boolean }
export interface ConversationSummary { id: Id; title: string; updatedAt: string; archived: boolean; pendingApprovals: number }
export interface Conversation extends ConversationSummary { messages: AgUiMessage[] }
export interface RetrievalStep { tool: string; operation: string; arguments: Record<string, unknown>; at: string; toolCallId: string }
export type RetrievalPath = RetrievalStep[];
export interface RetrievalRecord { messageId: Id; runId: Id; model: string; instructionsVersion: string; path: RetrievalPath }
export type WorkspaceFileState = "uploading" | "processing" | "ready" | "failed" | "export-pending" | "exported";
export interface WorkspaceFile { id: Id; conversationId: Id; name: string; mediaType: string; sizeBytes: number; origin: "upload" | "agent"; state: WorkspaceFileState; simulated: boolean; classification: Classification; createdAt: string; statusDetail?: string; exportApprovalId?: Id }

export interface WriteNote { title: string; body: string; conversationId?: Id | null }
export interface NoteList { conversationId?: Id | null }
export interface NoteRef { id: Id }
export interface NoteSummary { id: Id; title: string; conversationId: Id | null; sizeBytes: number; createdAt: string; updatedAt: string }
export interface Note extends NoteSummary { body: string }
export interface AskAgent { question: string; conversationId?: Id | null }
export interface AskAgentResult { answer: string; citations: Citation[]; confidence: Confidence; runId: Id }

export interface ChartInput { spec: Record<string, unknown>; format: "svg" | "png" }
export interface ChartResult { format: "svg" | "png"; mediaType: string; contentBase64: string | null; file: WorkspaceFile | null }
export interface CalculationInput { expression: string; craftResult?: string | number | null; unit?: string | null }
export interface CalculationResult { expression: string; wolframResult: string; craftResult: string | number | null; agrees: boolean; checkedAt: string }

export type ApprovalStatus = "pending" | "approved" | "rejected" | "executed" | "failed";
export interface Approval { id: Id; conversationId: Id; runId: Id; tool: string; summary: string; status: ApprovalStatus; requestedAt: string }
export interface ApprovalEvent { kind: "requested" | "approved" | "rejected" | "executed" | "failed"; actorId: Id | null; at: string; note?: string | null }
export interface ApprovalDetail extends Approval { arguments: Record<string, unknown>; evidence: Record<string, unknown>[]; reasoning: string; retrievalPath: Record<string, unknown>[]; events: ApprovalEvent[] }
export interface Decision { approve: boolean; note?: string | null }
