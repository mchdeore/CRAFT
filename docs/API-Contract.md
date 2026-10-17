# API Contract

<p class="meta">CRAFT | HTTP interface between the front end and the backend | Version 1.0 | 16 October 2026</p>

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP) with JavaScript Object Notation (JSON). Agent-User Interaction (AG-UI) events use Server-Sent Events (SSE). Identifiers are universally unique identifiers (UUIDs). Times follow the International Organization for Standardization (ISO) 8601 format in Coordinated Universal Time (UTC).

## 1. Rules

1. Sections 13 and 14 mirror the built `api/types.ts` and `api/routes.ts` forms. The extra forms in section 13 belong to table 5.2.
2. Ordinary routes use one HTTP request and one response. File routes return bytes.
3. The chat route takes AG-UI `RunAgentInput` and returns AG-UI events as SSE.
4. A contract change updates `api/`, the backend and the contract checks together.
5. Table 5.1 describes the app. Table 5.2 preserves the remaining agreements and their build steps.

## 2. Conventions

| Item | Rule |
|---|---|
| Prefix | `/api`. Health checks are outside it. There is no generic run-any-operation route. |
| Format | JSON, `camelCase` field names, UUID identifiers, ISO 8601 UTC times |
| Sign-in | Cookie `__Host-craft_session` (`Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`), set by `POST /api/session`. Send requests with `credentials: "include"`. No tokens in browser storage |
| Writes | Ordinary `/api` requests using `POST`, `PUT`, `PATCH` and `DELETE` must carry an `Origin` header from the allowed list |
| Request ID | Every response has `X-Request-Id`. The same value appears in error bodies and server logs |
| Unreadable records | `404`, the same as a missing record |
| Lists | Operation-specific limits; no paging. The conversation list agreement is newest first, at most 100 items |
| Files | Sent with their media type and `X-Content-Type-Options: nosniff`. Preview is inline only for PDF, images and plain text, under `Content-Security-Policy: sandbox`; other types download. Upload: raw bytes, the name in `X-File-Name` |

## 3. Errors

Errors from ordinary `/api` routes use this body. Readiness returns `Health` with status `unavailable` and HTTP 503; failures inside an accepted chat stream use `RUN_ERROR`.

```json
{ "code": "invalid_input", "message": "Invalid input.", "requestId": "0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e61",
  "fields": [{ "path": "title", "message": "Must not be empty." }] }
```

| Status | `code` | When |
|---|---|---|
| 400 | `invalid_input` | Bad body, query or path. `fields` lists each problem |
| 401 | `not_signed_in` | No session, or it expired |
| 403 | `forbidden` | Signed in, not permitted |
| 404 | `not_found` | Missing or unreadable record, unknown route or method |
| 409 | `conflict` | Already decided, a run already active, a reused run ID, or stale state |
| 413 | `too_large` | A request or file over its limit |
| 429 | `rate_limited` | Too many sign-in attempts or runs |
| 500 | `internal_error` | Unexpected failure. Diagnostics use safe error codes |

## 4. Limits

| Item | Limit |
|---|---|
| Chat request | 1 MiB; only the newest input message is accepted into server history |
| Sign-in attempts | 5 per minute per account name, then `429` |
| Agent runs | 1 active per conversation (`409`), 3 per account (`429`) |
| One run | 20 tool calls, 120 seconds |
| Library search | 1 to 500 query characters, at most 8 results |
| Chunk read | At most 20 blocks; explore before/after each 0 to 10, default 2 |
| Summary upload | 25 MB; at most 100,000 converted characters |
| Version differences | At most 500 blocks |
| Datacube find | 1 to 12 conditions; at most 20 missions |
| Datacube comparison | 2 to 5 missions |
| Similarity | At most 10 results; one to five distinct facets |
| Source decimal | At most 100 characters; no exponent, NaN or Infinity |

The workspace, draft, candidate submission and saved-view limits are described in sections 8 and 9 and table 5.2. They are separate from the enforced read limits.

## 5. Routes

### 5.1 Built

| Route | Request body or query | Response | Access | Agent tool |
|---|---|---|---|---|
| `GET /api/approvals` | —; query `{ status?: ApprovalStatus }` | `Approval[]` | signed in; requester ownership | — |
| `GET /api/approvals/:id` | — | `ApprovalDetail` | signed in; requester ownership | — |
| `POST /api/approvals/:id/decision` | `Decision` | `Approval` | HTTP requester only | — |
| `GET /api/tools` | — | `ToolInfo[]` | signed in; live resource access | — |
| `POST /api/agent/runs` | `RunAgentInput` | `AG-UI event stream` | signed in; live resource access | — |
| `POST /api/cube/candidates` | `PrepareValues` | `Candidates` | signed in; live resource access | `prepare_values` |
| `POST /api/library/citations/check` | `CitationCheck` | `CitationStatus` | signed in; live resource access | `check_citation` |
| `GET /api/library/documents/:item/compare` | —; query `{ fromVersion: number; toVersion: number }` | `VersionDiff` | signed in; live resource access | `compare_versions` |
| `GET /health/live` | — | `Health` | public | — |
| `GET /health/ready` | — | `Health` | public | — |
| `POST /api/library/search` | `LibrarySearch` | `LibraryHits` | signed in; live resource access | `search` |
| `GET /api/library/documents/:item` | —; query `{ version?: number }` | `LibraryDocument` | signed in; live resource access | `get_document` |
| `GET /api/library/documents/:item/chunks` | —; query `{ version: number; fromBlock: number; toBlock: number }` | `ChunkPage` | signed in; live resource access | `read_chunks` |
| `POST /api/library/explore` | `ExploreInput` | `ChunkPage` | signed in; live resource access | `explore` |
| `GET /api/library/documents/:item/preview` | —; query `{ version: number; locator?: string }` | `Blob` | signed in; live resource access | — |
| `GET /api/library/documents/:item/download` | —; query `{ version: number; locator?: string }` | `Blob` | signed in; live resource access | — |
| `POST /api/session` | `SignIn` | `Me` | public | — |
| `DELETE /api/session` | — | `void` | signed in; live resource access | — |
| `GET /api/me` | — | `Me` | signed in; live resource access | — |
| `GET /api/cube/missions` | —; query `{query?: string; limit?: number}` | `Mission[]` | signed in; live resource access | `list_missions` |
| `GET /api/cube/parameters` | — | `Parameter[]` | signed in; live resource access | `list_parameters` |
| `POST /api/cube/find` | `FindMissions` | `FoundMissions` | signed in; live resource access | `find_missions` |
| `GET /api/cube/missions/:key` | —; query `{silo?: string; stage?: string; at?: string; knownAt?: string}` | `MissionState` | signed in; live resource access | `get_mission` |
| `POST /api/cube/compare` | `CompareMissions` | `Comparison` | signed in; live resource access | `compare_missions` |
| `GET /api/cube/values/:id` | — | `ValueEvidence` | signed in; live resource access | `get_value_evidence` |
| `POST /api/summarize` | `SelectedSource` | `SummaryResult` | signed in; live resource access | — |
| `POST /api/summarize/upload` | `Blob` | `SummaryResult` | signed in; live resource access | — |
| `POST /api/cube/similar` | `SimilarMissions` | `SimilarPage` | signed in; live resource access | `similar_missions` |
| `POST /api/cube/similar/explain` | `ExplainSimilarity` | `SimilarityExplanation` | signed in; live resource access | `explain_similarity` |
| `GET /api/conversations` | — | `ConversationSummary[]` | signed in; owned conversation | — |
| `POST /api/conversations` | `NewConversation` | `ConversationSummary` | signed in; owned conversation | — |
| `GET /api/conversations/:id` | — | `Conversation` | signed in; owned conversation | — |
| `PATCH /api/conversations/:id` | `ConversationUpdate` | `ConversationSummary` | signed in; owned conversation | — |
| `GET /api/conversations/:id/files` | — | `WorkspaceFile[]` | signed in; owned conversation | — |
| `POST /api/conversations/:id/files` | `Blob`; query `{ classification?: T.Classification }` | `WorkspaceFile` | signed in; owned conversation | — |
| `GET /api/conversations/:id/files/:fileId/content` | — | `Blob` | signed in; owned conversation | — |
| `DELETE /api/conversations/:id/files/:fileId` | — | `void` | signed in; owned conversation | — |
| `PUT /api/notes` | `WriteNote` | `Note` | signed in; live resource access | `write_note` |
| `GET /api/notes` | —; query `{ conversationId?: string }` | `NoteSummary[]` | signed in; live resource access | `list_notes` |
| `GET /api/notes/:id` | — | `Note` | signed in; live resource access | `read_note` |
| `DELETE /api/notes/:id` | — | `void` | signed in; live resource access | `delete_note` |
| `POST /api/agent/ask` | `AskAgent` | `AskAgentResult` | signed in; live resource access | `ask_agent` |
| `POST /api/tools/chart` | `ChartInput` | `ChartResult` | signed in; live resource access | `make_chart` |
| `POST /api/tools/calculation-check` | `CalculationInput` | `CalculationResult` | signed in; live resource access | `check_calculation` |

### 5.2 Agreed, not built yet

| Route | Request | Response | Access and tool | Step |
|---|---|---|---|---|
| `POST /api/conversations/:id/files/:fileId/export` | `ExportRequest` | `WorkspaceFile`; creates an approval request | owner; no tool | B25 |
| `GET /api/drafts` | — | `DraftSummary[]` | own; `list_drafts` | B25 |
| `GET /api/drafts/:id` | — | `Draft` | owner; `read_draft` | B25 |
| `POST /api/drafts` | `NewDraft` | `Draft` | signed in; `save_draft`, pauses for approval | B25 |
| `GET /api/approvals` | query `{ status?: ApprovalStatus }` | `Approval[]` | readable requests; no tool | B24 |
| `GET /api/approvals/:id` | — | `ApprovalDetail` | requester access; no tool | B24 |
| `POST /api/approvals/:id/decision` | `Decision` | `Approval` | requester decision, never the agent; no tool | B24 |
| `POST /api/cube/values` | `SubmitValues` | `ValueReceipt` | signed in; `submit_values`, DATA, approval | B26 |
| `POST /api/cube/views` | `SaveView` | `WorkspaceFile` | signed in; `save_view`, DOCUMENT, approval | B27 |

These agreements include approval decisions and standard AG-UI interrupt/resume. A paused run is found by its run identifier in the pending approval list. The decision is read from the database; a new run uses a resolved or cancelled resume entry. Saved history is returned as AG-UI messages by the conversation route. The current run route refuses resume with `409`. Approved exports set `exported`; rejected execution writes nothing.

## 6. Agent tools

Input types in this table include path fields; ordinary GET routes carry the other fields as query parameters. Every tool is also an ordinary route and a checked Python operation. The runtime calls the registered read tools through native HTTP MCP. All built tool effects are `NONE`.

| Tool | Route | Input type | Effects |
|---|---|---|---|
| `ask_agent` | `POST /api/agent/ask` | `AskAgent` | `NONE` |
| `check_calculation` | `POST /api/tools/calculation-check` | `CalculationInput` | `NONE` |
| `check_citation` | `POST /api/library/citations/check` | `CitationCheck` | `NONE` |
| `compare_missions` | `POST /api/cube/compare` | `CompareMissions` | `NONE` |
| `compare_versions` | `GET /api/library/documents/:item/compare` | `CompareVersions` | `NONE` |
| `delete_note` | `DELETE /api/notes/:id` | `NoteRef` | `NONE` |
| `explain_similarity` | `POST /api/cube/similar/explain` | `ExplainSimilarity` | `NONE` |
| `explore` | `POST /api/library/explore` | `ExploreInput` | `NONE` |
| `find_missions` | `POST /api/cube/find` | `FindMissions` | `NONE` |
| `get_document` | `GET /api/library/documents/:item` | `DocumentRef` | `NONE` |
| `get_mission` | `GET /api/cube/missions/:key` | `MissionRef` | `NONE` |
| `get_value_evidence` | `GET /api/cube/values/:id` | `ValueRef` | `NONE` |
| `list_missions` | `GET /api/cube/missions` | `ListMissions` | `NONE` |
| `list_notes` | `GET /api/notes` | `NoteList` | `NONE` |
| `list_parameters` | `GET /api/cube/parameters` | `none` | `NONE` |
| `make_chart` | `POST /api/tools/chart` | `ChartInput` | `NONE` |
| `prepare_values` | `POST /api/cube/candidates` | `PrepareValues` | `NONE` |
| `read_chunks` | `GET /api/library/documents/:item/chunks` | `ChunkRange` | `NONE` |
| `read_note` | `GET /api/notes/:id` | `NoteRef` | `NONE` |
| `search` | `POST /api/library/search` | `LibrarySearch` | `NONE` |
| `similar_missions` | `POST /api/cube/similar` | `SimilarMissions` | `NONE` |
| `write_note` | `PUT /api/notes` | `WriteNote` | `NONE` |

`/mcp` is a stateless JSON MCP mount outside the ordinary route map. It reuses the same registry server as standard input and output. The server lists every `tool=True` operation; the native framework transport applies its read-tool allowlist. Private handlers and observations are absent. Effectful calls return `approval_required`; a run credential grants no write approval.

Authentication requires `Authorization: Bearer` with the current run credential. Missing, malformed, unknown, expired or revoked credentials return HTTP 401 and `WWW-Authenticate: Bearer` before a protocol tool executes. A browser cookie alone is refused. Tool errors carry safe code/message objects in structured and text content. `craft/toolCallId` metadata links the model call; identity and parent context load from server-owned state. Raw credentials stay outside browser storage and model arguments. The loopback target and lifetime follow section 3 of the specification.

## 7. Chat stream

The chat route accepts AG-UI's `RunAgentInput`. `threadId` identifies the conversation. `runId` is a new UUID echoed in `RUN_STARTED`; reuse returns `409`. The server reads only those identifiers, the newest input message in `messages`, and refuses `resume`. It loads history, tools, context and state itself. The response is `text/event-stream`, with one AG-UI event per `data:` line. The body limit is 1 MiB. The current read-only runtime ignores browser context, tools, state, instructions and identity, and appends only the newest input text. Resume remains unavailable and returns conflict. Unknown fields cannot replace server state.

The complete synthetic stream is `api/examples/chat-answer.sse`.

| Event | Meaning |
|---|---|
| `RUN_STARTED` | The turn was accepted and a run exists |
| `TEXT_MESSAGE_START`, `_CONTENT`, `_END` | One output message, in pieces |
| `TOOL_CALL_START`, `_ARGS`, `_END`, `_RESULT` | A tool call, its arguments and a short result summary |
| `ACTIVITY_SNAPSHOT`, type `craft.answer` | Citations and confidence for the message just finished; saved as an activity message, so the history shows them too |
| `RUN_FINISHED` without an interrupt | The turn is complete and saved |
| `RUN_ERROR` | The turn failed with a safe error code. Saved history stays |

## 8. Conversation workspace

Conversation and file routes use owned PostgreSQL records. Saved history includes standard AG-UI text, tool and activity messages. A separate `craft.retrieval` activity records the answer message, run, model, instruction version and ordered tool/arguments/time path. Later runs retain earlier answer paths. Private transport fields are excluded from public message envelopes.

1. Upload: `POST /api/conversations/:id/files` with the raw bytes and the name in `X-File-Name`. The file is `uploading`, then `processing` while it is read, then `ready` or `failed`; `statusDetail` says why.
2. Listing, downloading and deleting are for the conversation's owner. Every download is recorded in the call log.
3. A file is labelled by what went into it: `simulated` only if all of it is, and the highest `classification` among it.
4. Archive refuses an active or paused run and deletes its workspace. History remains readable. Sign-out deletes nothing. Export needs approval. `POST …/export` creates an approval request and sets `export-pending`; approved execution sets `exported`.

The enforced limits are 25 MiB per file and 50 files per conversation. Conversation and draft titles contain 1 to 200 characters; draft bodies contain at most 100,000 characters.

Uploads carry `X-Simulated`; their sole source classification is a validated query value, defaulting to unclassified. Bytes stay in PostgreSQL, and filenames never select a filesystem path. Upload records pass through uploading, processing and ready. Downloads return exact bytes as an attachment with nosniff.

## 9. Datacube

Datacube operations use the access, error, request, approval and stream conventions above. Section 8 of the specification defines their behavior. Unknown parameters, units, facets or stages return `invalid_input`; unreadable missions, values or handles return `not_found`.

Find limits: 1 to 12 conditions and at most 20 missions in `find_missions`; 2 to 5 missions in `compare_missions`; at most 10 results in `similar_missions`; The table 5.2 agreements allow at most 100 submitted candidates and 200 rows in a saved view. `at` is a date (`YYYY-MM-DD`) and `knownAt` a timestamp; both default to the time of the request and are returned. Source numbers are decimal strings of at most 100 characters without exponent, NaN or Infinity.

Similarity selects all five facets by default. An explicit selection has one to five distinct names; duplicate or unknown names are invalid. Scores range from -1 to 1. Missing or unreadable facet texts have null sides and no source identifiers; missing current-model vectors yield null scores. At least half the selected facets, rounded up, must count. Comparable results sort by descending mean score and then mission key; other results follow. The JSON examples are `api/examples/similar-missions.json` and `api/examples/similarity-explanation.json`.

## 10. Document summary

`GET /summarize` returns one static sign-in and summary shell with the development page disabled. The two summary APIs require the real cookie and an allowed Origin. They create no draft, filing, workspace file, mission or accepted value. Originals remain in request and browser File memory only.

Uploads accept PDF, docx and xlsx with their actual media types. The limit is 25 MB, with at most 100,000 converted characters for summary input. An absent simulation label is refused. Malformed or unsupported input is `400`; an oversized source is `413`; an unknown, unreadable or hash-mismatched selected version is `404`. Unsupported model sentences or filing claims return safe `500` without a partial accepted summary.

`SummarySource.version` hashes exact original bytes. `blockVersion` hashes the full normalized block quote. A selected citation keeps the provider handle; an upload has no provider handle or version. Every sentence cites complete supporting source context. Provider-path evidence supplies selected tags; upload filing evidence includes source headings and quotes. Missing or conflicting filing values are null.

Until the six-input confidence calibration is defined, scores remain zero, the level is low and status is `insufficient_evidence`. Cited extracts remain visible and no draft or filing is written. Model self-rating is ignored. Upload originals download from the owned File object; exact quoted blocks remain readable on the page. Tokens never enter browser storage.

## 11. Version differences and citation checks

`CompareVersions` names one readable item and two positive integer versions. The response contains at most 500 ordered added, removed, changed or moved blocks, with nullable source and target positions and a truncation flag. Equal versions return an empty list. Missing or unreadable versions return 404.

`CitationCheck` holds one canonical handle. It returns current, changed, newer_version or withdrawn and the latest provider version. The original is re-read outside database transactions. A row locator checks only its table row. Live source permissions apply before original bytes are read. Nonpositive positions return 400; a missing or mismatched quote returns changed. No source text is returned by these two operations.

## 12. Table candidate values

`POST /api/cube/candidates` accepts `PrepareValues` and returns `Candidates`. The handle must name a complete readable table chunk; a non-table input is 400 and a mismatched handle is 404. Mission, silo and stage default to source tags. A named mission must be readable, including its selected silo. Unknown scope keys or invalid dates are 400. A missing date is a problem and is never guessed.

Each candidate contains its source row, header, parameter, named element or null mission total, original numeric token and unit, converted interval, row citation and at most one problem. Numeric tokens are at most 100 characters. Cells without valid numbers use an empty source-number string; their source text remains available through the row citation. Empty and TBD cells have kind unknown; n/a has kind not_applicable. Neither has numeric bounds. Ranges convert both decimal bounds. Candidate preparation writes no domain rows.

Problem names are unknown header, no unit, unknown unit, not a number, outside the allowed bounds, mission not resolved and no valid-from date. A candidate takes the first applicable problem in that order. The top-level problems list includes every applicable scope and cell problem. Element recognition applies account grants even when no silo is specified. Explicit element names remain names; no unrecognized row is treated as a mission total.

## 13. `api/types.ts`

Built forms match `api/types.ts`. The final group records the remaining table 5.2 agreements. AG-UI messages use the protocol package schema.

```ts
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

// Remaining forms for table 5.2.
export type Timestamp = string;   // ISO 8601, UTC

export interface ExportRequest { destination: string; justification: string }            // S4

export interface NewDraft { title: string; body: string; basedOn?: Id }                   // S4

export interface DraftSummary { id: Id; title: string; basedOn: Id | null; createdAt: Timestamp }

export interface Draft extends DraftSummary { body: string }

export type ApprovalStatus = "pending" | "approved" | "rejected" | "executed" | "failed";

export interface Approval {
  id: Id; conversationId: Id; runId: Id; tool: string; summary: string;
  status: ApprovalStatus; requestedAt: Timestamp;
}

export interface ApprovalDetail extends Approval {
  arguments: Record<string, unknown>; evidence: Citation[]; confidence?: Confidence;
  reasoning: string;                   // why the agent requests this action
  retrievalPath: RetrievalPath;        // how it found the evidence
  events: ApprovalEvent[];
}

export interface ApprovalEvent {
  kind: "requested" | "approved" | "rejected" | "executed" | "failed";
  actorId: Id | null; at: Timestamp; note?: string;                  // null actor = the system
}

export interface Decision { approve: boolean; note?: string }

export interface SubmitValues {
  mission: string; silo: string; stage: string | null; validFrom: string;
  candidates: Candidate[];             // only candidates without a problem
}

export interface ValueReceipt { approvalId: Id; valueIds: Id[] }

export interface SaveView {
  conversationId: Id; name: string;
  operation: "find_missions" | "get_mission" | "compare_missions" | "similar_missions";
  input: Record<string, unknown>;      // that operation's request
}
```

## 14. `api/routes.ts`

```ts
import type * as T from "./types";
import type { RunAgentInput, Event as AGUIEvent } from "@ag-ui/core";
export interface Routes {
  "GET /api/tools": { response: T.ToolInfo[] };
  "POST /api/agent/runs": { body: RunAgentInput; stream: AGUIEvent };
  "POST /api/cube/candidates": { body: T.PrepareValues; response: T.Candidates };
  "POST /api/library/citations/check": { body: T.CitationCheck; response: T.CitationStatus };
  "GET /api/library/documents/:item/compare": { query: { fromVersion: number; toVersion: number }; response: T.VersionDiff };
  "GET /health/live": { response: T.Health };
  "GET /health/ready": { response: T.Health };
  "POST /api/library/search": { body: T.LibrarySearch; response: T.LibraryHits };
  "GET /api/library/documents/:item": { query: { version?: number }; response: T.LibraryDocument };
  "GET /api/library/documents/:item/chunks": { query: { version: number; fromBlock: number; toBlock: number }; response: T.ChunkPage };
  "POST /api/library/explore": { body: T.ExploreInput; response: T.ChunkPage };
  "GET /api/library/documents/:item/preview": { query: { version: number; locator?: string }; response: Blob };
  "GET /api/library/documents/:item/download": { query: { version: number; locator?: string }; response: Blob };
  "POST /api/session": { body: T.SignIn; response: T.Me };
  "DELETE /api/session": { response: void };
  "GET /api/me": { response: T.Me };
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
  "PATCH /api/conversations/:id": { body: T.ConversationUpdate; response: T.ConversationSummary };
  "GET /api/conversations/:id/files": { response: T.WorkspaceFile[] };
  "POST /api/conversations/:id/files": { body: Blob; query: { classification?: T.Classification }; response: T.WorkspaceFile };
  "GET /api/conversations/:id/files/:fileId/content": { response: Blob };
  "DELETE /api/conversations/:id/files/:fileId": { response: void };

  "PUT /api/notes": { body: T.WriteNote; response: T.Note };
  "GET /api/notes": { query: { conversationId?: string }; response: T.NoteSummary[] };
  "GET /api/notes/:id": { response: T.Note };
  "DELETE /api/notes/:id": { response: void };
  "POST /api/agent/ask": { body: T.AskAgent; response: T.AskAgentResult };
  "POST /api/tools/chart": { body: T.ChartInput; response: T.ChartResult };
  "POST /api/tools/calculation-check": { body: T.CalculationInput; response: T.CalculationResult };
}
```

## 15. Examples

Every built JSON route has a synthetic response example. Binary and `204` responses have no JSON body. Chat uses `api/examples/chat-answer.sse`.

| Route | Response type | File |
|---|---|---|
| `GET /api/approvals` | `Approval[]` | `api/examples/approvals.json` |
| `GET /api/approvals/:id` | `ApprovalDetail` | `api/examples/approval-detail.json` |
| `POST /api/approvals/:id/decision` | `Approval` | `api/examples/approval.json` |
| `GET /api/tools` | `ToolInfo[]` | `api/examples/tools.json` |
| `POST /api/cube/candidates` | `Candidates` | `api/examples/candidates.json` |
| `POST /api/library/citations/check` | `CitationStatus` | `api/examples/citation-status.json` |
| `GET /api/library/documents/:item/compare` | `VersionDiff` | `api/examples/version-diff.json` |
| `GET /health/live` | `Health` | `api/examples/health-live.json` |
| `GET /health/ready` | `Health` | `api/examples/health-ready.json` |
| `POST /api/library/search` | `LibraryHits` | `api/examples/library-search.json` |
| `GET /api/library/documents/:item` | `LibraryDocument` | `api/examples/library-document.json` |
| `GET /api/library/documents/:item/chunks` | `ChunkPage` | `api/examples/chunks.json` |
| `POST /api/library/explore` | `ChunkPage` | `api/examples/explore.json` |
| `POST /api/session` | `Me` | `api/examples/session.json` |
| `GET /api/me` | `Me` | `api/examples/me.json` |
| `GET /api/cube/missions` | `Mission[]` | `api/examples/missions.json` |
| `GET /api/cube/parameters` | `Parameter[]` | `api/examples/parameters.json` |
| `POST /api/cube/find` | `FoundMissions` | `api/examples/found-missions.json` |
| `GET /api/cube/missions/:key` | `MissionState` | `api/examples/mission-state.json` |
| `POST /api/cube/compare` | `Comparison` | `api/examples/comparison.json` |
| `GET /api/cube/values/:id` | `ValueEvidence` | `api/examples/value-evidence.json` |
| `POST /api/summarize` | `SummaryResult` | `api/examples/summarize-selected.json` |
| `POST /api/summarize/upload` | `SummaryResult` | `api/examples/summarize-uploaded.json` |
| `POST /api/cube/similar` | `SimilarPage` | `api/examples/similar-missions.json` |
| `POST /api/cube/similar/explain` | `SimilarityExplanation` | `api/examples/similarity-explanation.json` |
| `GET /api/conversations` | `ConversationSummary[]` | `api/examples/conversations.json` |
| `POST /api/conversations` | `ConversationSummary` | `api/examples/conversation-summary.json` |
| `GET /api/conversations/:id` | `Conversation` | `api/examples/conversation.json` |
| `PATCH /api/conversations/:id` | `ConversationSummary` | `api/examples/conversation-summary.json` |
| `GET /api/conversations/:id/files` | `WorkspaceFile[]` | `api/examples/workspace-files.json` |
| `POST /api/conversations/:id/files` | `WorkspaceFile` | `api/examples/workspace-file.json` |
| `PUT /api/notes` | `Note` | `api/examples/note.json` |
| `GET /api/notes` | `NoteSummary[]` | `api/examples/notes.json` |
| `GET /api/notes/:id` | `Note` | `api/examples/note.json` |
| `POST /api/agent/ask` | `AskAgentResult` | `api/examples/ask-agent.json` |
| `POST /api/tools/chart` | `ChartResult` | `api/examples/chart.json` |
| `POST /api/tools/calculation-check` | `CalculationResult` | `api/examples/calculation-check.json` |

## 16. Stand-ins and contract tests

`api/examples/` holds synthetic responses and the complete chat stream. JSON examples are stand-ins with the same response forms as the real endpoints. Demonstrations use the real backend and a synthetic seed set.

1. `api/*.ts` converts to JSON Schema with the held TypeScript generator.
2. Built route rows equal the app's method/path pairs in both directions. Tool rows equal the registry's tool names.
3. Python forms match their TypeScript fields and required fields in the component contract checks.
4. Every built JSON route names an example. Examples validate against compiled response schemas. File responses are checked by status and media type.
5. Recorded and fake-model chat events validate against `@ag-ui/core`; `craft.answer` also validates against `AnswerMeta`.

The two tables are checked by `test_contract_routes_match_app` and `test_contract_tools_match_registry` in `app/tests/test_contract.py`. The examples are checked by `test_contract_json_examples`. Any added or removed endpoint or tool requires a matching row in the same change.
