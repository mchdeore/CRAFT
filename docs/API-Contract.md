# API Contract

<p class="meta">CRAFT | HTTP interface between the front end and the backend | Version 1.0 | 14 October 2026</p>

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP) with JavaScript Object Notation (JSON). Agent-User Interaction (AG-UI) events use Server-Sent Events (SSE). Identifiers are universally unique identifiers (UUIDs). Times follow the International Organization for Standardization (ISO) 8601 format in Coordinated Universal Time (UTC).

## 1. Rules

1. The TypeScript declarations in sections 7 and 8 define the API forms. The operation registry exports them as `api/types.ts` and `api/routes.ts`. The front end and backend must adhere to these forms.
2. Ordinary routes are HTTP with JSON bodies: one request, one response.
3. The chat route takes AG-UI's request (`RunAgentInput`) and returns a stream of AG-UI events (an open protocol for agent runs) as server-sent events.
4. A contract change updates `api/`, the backend and their checks in the same pull request.
5. Routes arrive slice by slice (column "Slice" in section 5). Until a route exists, its specified example response serves as a stand-in.

## 2. Conventions

| Item | Rule |
|---|---|
| Prefix | `/api`. Health checks are outside it. There is no generic run-any-operation route. |
| Format | JSON, `camelCase` field names, UUID identifiers, ISO 8601 UTC times |
| Sign-in | Cookie `__Host-craft_session`, set by `POST /api/session`. Send requests with `credentials: "include"`. No tokens in browser storage |
| Writes | `POST`, `PUT`, `PATCH` and `DELETE` must carry an `Origin` header from the allowed list |
| Request ID | Every response has `X-Request-Id`. The same value appears in error bodies and server logs |
| Other users' records | `404`, the same as a missing record |
| Lists | Newest first, at most 100 items, no paging |
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
| Chat message | 8,000 characters (the newest user message) |
| Chat request | 1 MB (AG-UI browser libraries resend the conversation; the server reads only the newest message) |
| Workspace | 25 MB per file, 50 files per conversation |
| Conversation or draft title | 1 to 200 characters |
| Draft body | 100,000 characters |
| Search query | 1 to 500 characters, at most 20 results |
| Sign-in attempts | 5 per minute per username, then `429` |
| Agent runs | 1 active per conversation (`409`), 3 per user (`429`) |
| One run | 20 tool calls, 120 seconds |

## 5. Routes

| Route | Request body | Response | Access | Agent tool | Slice |
|---|---|---|---|---|---|
| `GET /health/live` | | `Health` | anyone | | S0 |
| `GET /health/ready` | | `Health`, `503` when not ready | anyone | | S0 |
| `GET /api/tools` | | `ToolInfo[]` | signed in | | S1 |
| `POST /api/library/search` | `LibrarySearch` | `LibraryHits` | readable missions | `search` | S1 |
| `GET /api/library/documents/:item?version=` | | `LibraryDocument` | readable | `get_document` | S1 |
| `GET /api/library/documents/:item/chunks?version=&fromBlock=&toBlock=` | | `ChunkPage` | readable | `read_chunks` | S1 |
| `POST /api/library/explore` | `ExploreInput` | `ChunkPage` | readable | `explore` | S1 |
| `GET /api/library/documents/:item/preview?version=&locator=` | | The original file, inline | readable | | S1 |
| `GET /api/library/documents/:item/download?version=` | | The original file, as an attachment | readable | | S1 |
| `POST /api/library/citations/check` | `CitationCheck` | `CitationStatus` | readable | `check_citation` | S3 |
| `GET /api/library/documents/:item/compare?fromVersion=&toVersion=` | | `VersionDiff` | readable | `compare_versions` | S3 |
| `POST /api/session` | `SignIn` | `Me` and the cookie | anyone | | S2 |
| `DELETE /api/session` | | `204` | signed in | | S2 |
| `GET /api/me` | | `Me` | signed in | | S2 |
| `GET /api/conversations` | | `ConversationSummary[]` | own | | S3 |
| `POST /api/conversations` | `NewConversation` | `ConversationSummary` | signed in | | S3 |
| `GET /api/conversations/:id` | | `Conversation` | owner | | S3 |
| `PATCH /api/conversations/:id` | `ConversationUpdate` | `ConversationSummary` | owner | | S3 |
| `POST /api/agent/runs` | `RunAgentInput` | AG-UI event stream | owner | | S3 |
| `GET /api/conversations/:id/files` | | `WorkspaceFile[]` | owner | | S3 |
| `POST /api/conversations/:id/files` | The file's bytes | `WorkspaceFile` | owner | | S3 |
| `GET …/files/:fileId/content` | | The file, as an attachment | owner | | S3 |
| `DELETE …/files/:fileId` | | `204` | owner | | S3 |
| `POST …/files/:fileId/export` | `ExportRequest` | `WorkspaceFile`; creates an approval request | owner | | S4 |
| `GET /api/drafts` | | `DraftSummary[]` | own | `list_drafts` | S4 |
| `GET /api/drafts/:id` | | `Draft` | owner | `read_draft` | S4 |
| `POST /api/drafts` | `NewDraft` | `Draft` | signed in | `save_draft`, pauses for approval | S4 |
| `GET /api/approvals?status=` | | `Approval[]` | readable requests | | S4 |
| `GET /api/approvals/:id` | | `ApprovalDetail` | requester access | | S4 |
| `POST /api/approvals/:id/decision` | `Decision` | `Approval` | requester decision, never the agent | | S4 |

`…` stands for `/api/conversations/:id`. Signed-in routes answer `401` until sign-in exists (S2); in S1 they are reached from Python and the command line. Every agent tool is also a route, so anything the agent does, the front end or a script can do directly.

## 6. Examples

The following examples use synthetic accounts and documents.

```http
POST /api/session
Origin: http://localhost:8000
Content-Type: application/json

{ "username": "demo", "password": "example-passphrase" }

200 OK
Set-Cookie: __Host-craft_session=…; Secure; HttpOnly; SameSite=Lax; Path=/
{ "id": "0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e61", "username": "demo", "displayName": "Demo User", "roles": ["member"] }
```

```http
POST /api/library/search
Content-Type: application/json

{ "query": "radiator thermal margin", "missions": ["msn-sim-01"] }

200 OK
{ "hits": [ { "kind": "chunk", "title": "Thermal Control Plan", "headingPath": "4 Thermal > 4.2 Radiator margins",
    "text": "…a margin of 5 K at end of life…", "score": 0.0325,
    "handle": { "item": "itm_4f9c2a0b1c2d", "version": 2, "block": 12, "locator": "heading=4.2;block=1",
                "quoteSha256": "c41d…", "simulated": true },
    "tags": { "mission": "msn-sim-01", "silo": "engineering", "stage": "st3-design" },
    "classification": "unclassified", "before": null, "after": null } ],
  "truncated": false }
```

```http
GET /api/drafts/0199e7c4-5b1e-7a40-9f0e-3c1d2b8a4e61            (a draft that belongs to someone else)

404 Not Found
{ "code": "not_found", "message": "Not found.", "requestId": "0199e7c5-5b1e-7a40-9f0e-3c1d2b8a4e61" }
```

## 7. `api/types.ts`

```ts
import type { Message as AgUiMessage } from "@ag-ui/core";

export type Id = string;          // UUID
export type Timestamp = string;   // ISO 8601, UTC

export interface Health { status: "ok" | "unavailable" }                                  // S0
export interface FieldError { path: string; message: string }
export interface ApiError { code: string; message: string; requestId: string; fields?: FieldError[] }

export type Effects = "none" | "data" | "document" | "external" | "final";               // S1
export interface ToolInfo {
  name: string; operation: string; description: string;
  effects: Effects; needsApproval: boolean; route: string;
}
export type Classification = "unclassified" | "protected-a" | "protected-b";
export interface CitationHandle {
  item: string; version: number; block: number; locator: string;
  quoteSha256: string;                 // SHA-256 of the quoted text
  simulated: boolean;
}
export interface Tags { mission: string | null; silo: string | null; stage: string | null }
export interface LibrarySearch {
  query: string; missions?: string[]; silos?: string[]; stages?: string[];
  kinds?: ("chunk" | "document")[]; limit?: number;               // 1 to 8, default 8
}
export interface ChunkText { handle: CitationHandle; headingPath: string; text: string; score?: number }
export interface LibraryHit {
  kind: "chunk" | "document"; handle: CitationHandle; title: string; headingPath: string;
  text: string; score: number; tags: Tags; classification: Classification;
  before: ChunkText | null; after: ChunkText | null;               // neighbours of a chunk
}
export interface LibraryHits { hits: LibraryHit[]; truncated: boolean }
export interface DocumentRef { item: string; version?: number }    // the latest version when omitted
export interface LibraryDocument {
  item: string; version: number; title: string; path: string; tags: Tags;
  summary: string; summaryKind: "extract" | "generated";
  outline: { block: number; headingPath: string }[];
  versions: { version: number; sha256: string; indexedAt: Timestamp }[];
  simulated: boolean; classification: Classification; deleted: boolean;
}
export interface ChunkRange { item: string; version: number; fromBlock: number; toBlock: number }  // at most 20
export interface ExploreInput { handle: CitationHandle; before?: number; after?: number; query?: string }  // 0 to 10, default 2
export interface ChunkPage { chunks: ChunkText[]; truncated: boolean }
export interface CitationCheck { handle: CitationHandle }
export interface CitationStatus { status: "current" | "changed" | "newer_version" | "withdrawn"; latestVersion: number }
export interface CompareVersions { item: string; fromVersion: number; toVersion: number }
export interface VersionDiff {
  blocks: { change: "added" | "removed" | "changed" | "moved"; fromBlock: number | null; toBlock: number | null }[];
  truncated: boolean;
}

export type Role = "member" | "admin";                                                    // S2
export interface SignIn { username: string; password: string }
export interface Me { id: Id; username: string; displayName: string; roles: Role[] }

export interface NewConversation { title?: string }                                      // S3
export interface ConversationUpdate { title?: string; archived?: boolean }
export interface ConversationSummary {
  id: Id; title: string; updatedAt: Timestamp; archived: boolean; pendingApprovals: number;
}
export interface Conversation extends ConversationSummary {
  messages: AgUiMessage[];             // user, assistant (with toolCalls), tool, activity
}
export interface Citation {
  handle: CitationHandle;              // item, version, block, locator, quote hash
  title: string; headingPath: string;
  classification: Classification;
  quote: string;
}
export interface Confidence { score: number; level: "high" | "medium" | "low"; reason: string }
export interface AnswerMeta { messageId: Id; citations: Citation[]; confidence: Confidence }  // craft.answer

export type WorkspaceFileState =
  | "uploading" | "processing" | "ready" | "failed" | "export-pending" | "exported";
export interface WorkspaceFile {
  id: Id; conversationId: Id; name: string; mediaType: string; sizeBytes: number;
  origin: "upload" | "agent";          // added by the user or made by the agent
  state: WorkspaceFileState;
  simulated: boolean;                  // only if everything put in it is simulated
  classification: Classification;      // the highest of what was put in it
  createdAt: Timestamp;
  statusDetail?: string;               // e.g. "Parsed 14 pages"
  exportApprovalId?: Id;               // set once an export is requested
}
export interface ExportRequest { destination: string; justification: string }            // S4

export interface NewDraft { title: string; body: string; basedOn?: Id }                   // S4
export interface DraftSummary { id: Id; title: string; basedOn: Id | null; createdAt: Timestamp }
export interface Draft extends DraftSummary { body: string }

export type ApprovalStatus = "pending" | "approved" | "rejected" | "executed" | "failed";
export interface Approval {
  id: Id; conversationId: Id; runId: Id; tool: string; summary: string;
  status: ApprovalStatus; requestedAt: Timestamp;
}
export interface RetrievalStep {
  tool: string;                        // e.g. "search"
  arguments: Record<string, unknown>;  // the parameters it was called with
  at: Timestamp;
}
export type RetrievalPath = RetrievalStep[];    // in order; its length is the iteration count
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
```

Types stay plain (interfaces, string unions, arrays, optional fields, `Record<string, unknown>`) so the JSON Schema generator can read them. The one exception, `AgUiMessage`, is checked with `@ag-ui/core`'s own schema.

## 8. `api/routes.ts`

```ts
import type { AGUIEvent, RunAgentInput } from "@ag-ui/core";
import type * as T from "./types";

export interface Routes {
  "GET /health/live":                 { response: T.Health };
  "GET /health/ready":                { response: T.Health };
  "GET /api/tools":                   { response: T.ToolInfo[] };
  "POST /api/library/search":         { body: T.LibrarySearch; response: T.LibraryHits };
  "GET /api/library/documents/:item": { query: { version?: number }; response: T.LibraryDocument };
  "GET /api/library/documents/:item/chunks": { query: { version: number; fromBlock: number; toBlock: number };
                                        response: T.ChunkPage };
  "POST /api/library/explore":        { body: T.ExploreInput; response: T.ChunkPage };
  "GET /api/library/documents/:item/preview":  { query: { version: number; locator?: string };
                                        response: Blob };                  // the original, inline
  "GET /api/library/documents/:item/download": { query: { version: number }; response: Blob };  // attachment
  "POST /api/library/citations/check": { body: T.CitationCheck; response: T.CitationStatus };
  "GET /api/library/documents/:item/compare": { query: { fromVersion: number; toVersion: number };
                                        response: T.VersionDiff };
  "POST /api/session":                { body: T.SignIn; response: T.Me };
  "DELETE /api/session":              { response: void };
  "GET /api/me":                      { response: T.Me };
  "GET /api/conversations":           { response: T.ConversationSummary[] };
  "POST /api/conversations":          { body: T.NewConversation; response: T.ConversationSummary };
  "GET /api/conversations/:id":       { response: T.Conversation };
  "PATCH /api/conversations/:id":     { body: T.ConversationUpdate; response: T.ConversationSummary };
  "GET /api/conversations/:id/files": { response: T.WorkspaceFile[] };
  "POST /api/conversations/:id/files": { body: Blob; response: T.WorkspaceFile };  // X-File-Name
  "GET /api/conversations/:id/files/:fileId/content": { response: Blob };         // attachment
  "DELETE /api/conversations/:id/files/:fileId":      { response: void };
  "POST /api/conversations/:id/files/:fileId/export": { body: T.ExportRequest; response: T.WorkspaceFile };
  "POST /api/agent/runs":             { body: RunAgentInput; stream: AGUIEvent };  // resume is part of it
  "GET /api/drafts":                  { response: T.DraftSummary[] };
  "GET /api/drafts/:id":              { response: T.Draft };
  "POST /api/drafts":                 { body: T.NewDraft; response: T.Draft };
  "GET /api/approvals":               { query: { status?: T.ApprovalStatus }; response: T.Approval[] };
  "GET /api/approvals/:id":           { response: T.ApprovalDetail };
  "POST /api/approvals/:id/decision": { body: T.Decision; response: T.Approval };
}
```

## 9. Chat stream

The chat route accepts AG-UI's `RunAgentInput`. `threadId` identifies the conversation. `runId` is a new UUID echoed in `RUN_STARTED`; reuse returns `409`. The server reads only those identifiers, the newest user message in `messages`, or `resume` after a pause. It loads history, tools, context and state itself. The response is `text/event-stream`, with one AG-UI event per `data:` line.

```text
data: {"type":"RUN_STARTED","threadId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e61","runId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e62"}
data: {"type":"TOOL_CALL_START","toolCallId":"call_1","toolCallName":"search"}
data: {"type":"TOOL_CALL_ARGS","toolCallId":"call_1","delta":"{\"query\":\"radiator margin\"}"}
data: {"type":"TOOL_CALL_END","toolCallId":"call_1"}
data: {"type":"TOOL_CALL_RESULT","toolCallId":"call_1","messageId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e70","content":"8 chunks"}
data: {"type":"TEXT_MESSAGE_START","messageId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e63","role":"assistant"}
data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e63","delta":"The radiator margin is 5 K."}
data: {"type":"TEXT_MESSAGE_END","messageId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e63"}
data: {"type":"ACTIVITY_SNAPSHOT","messageId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e64","activityType":"craft.answer","content":{"messageId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e63","citations":[{"passageId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e66","documentId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e65","title":"Thermal Control Plan","section":"4.2 Radiator margins","version":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","artifact":"thermal-plan.pdf","locator":"4.2","simulated":true,"classification":"unclassified","quote":"The radiator margin is 5 K."}],"confidence":{"score":0.86,"level":"high","reason":"The figure matches the cited source."}}}
data: {"type":"RUN_FINISHED","threadId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e61","runId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e62"}
```

| Event | Meaning |
|---|---|
| `RUN_STARTED` | The turn was accepted and a run exists |
| `TEXT_MESSAGE_START`, `_CONTENT`, `_END` | One assistant message, in pieces |
| `TOOL_CALL_START`, `_ARGS`, `_END`, `_RESULT` | A tool call, its arguments and a short result summary |
| `ACTIVITY_SNAPSHOT`, type `craft.answer` | Citations and confidence for the message just finished; saved as an activity message, so the history shows them too |
| `RUN_FINISHED` with `outcome.type = "interrupt"` | The run is paused for a decision on an approval request |
| `RUN_FINISHED` without an interrupt | The turn is complete and saved |
| `RUN_ERROR` | The turn failed with a safe error code. Saved history stays |

Resuming after a pause:

1. `GET /api/approvals?status=pending` returns the request whose `runId` matches the paused run.
2. The requester approves or rejects using `POST /api/approvals/:id/decision`. The agent cannot decide.
3. Send `POST /api/agent/runs` with the standard AG-UI input: the conversation identifier, a new run identifier, messages and a resume entry naming the interrupt. A `resolved` status continues the turn; `cancelled` cancels the request. The server reads the decision from its database.

If the stream drops, `GET /api/conversations/:id` returns the saved messages as AG-UI messages, which the front end reads with the same code as the live stream, and a paused run appears in the approvals list and in `pendingApprovals`.

## 10. Conversation workspace

Each conversation has a workspace: files a person uploads or the agent makes.

1. Upload: `POST /api/conversations/:id/files` with the raw bytes and the name in `X-File-Name`. The file is `uploading`, then `processing` while it is read, then `ready` or `failed`; `statusDetail` says why.
2. Listing, downloading and deleting are for the conversation's owner. Every download is recorded in the call log.
3. A file is labelled by what went into it: `simulated` only if all of it is, and the highest `classification` among it.
4. Archiving the conversation deletes its workspace. Sign-out deletes nothing. Export needs approval. `POST …/export` creates an approval request and sets `export-pending`; approved execution sets `exported`.

## 11. Stand-ins and contract tests

`api/examples/` holds one JSON response per route and two recorded streams: `chat-answer.sse` (text, a tool call and `craft.answer`) and `chat-approval.sse` (a pause). The front end can build against them with any request mock; replacing a stand-in with the real route changes only the base URL. Demonstrations use the real backend with a seed data set.

Required integration checks, introduced with the executable contract:

1. `api/*.ts` converts to JSON Schema (the TypeScript schema generator).
2. Server routes equal the `Routes` keys, for the routes built so far.
3. Each Python shape matches its TypeScript type: same fields, same required set.
4. Every JSON test response and every file in `api/examples/` validates against its schema. File responses are checked by status and media type.
5. From S3, every event of a fake-model chat, and every message `GET /api/conversations/:id` returns, validates against `@ag-ui/core`.

Deferred: `/api/v2` when another front end needs a breaking change; paging; routes for roles, designated approvers and Microsoft Entra ID sign-in; export destinations; before real data, downloads of protected files only through an approved export.

## 12. Datacube routes and limits

Datacube operations use the access, error, request, approval and stream conventions above. Section 8 of the specification defines their behavior. Unknown parameters, units, facets or stages return `invalid_input`; unreadable missions, values or handles return `not_found`.

| Operation | Route | Request | Response | Tool | Effects | Slice |
|---|---|---|---|---|---|---|
| `cube.list_parameters` | `GET /api/cube/parameters` | | `Parameter[]` | `list_parameters` | NONE | S3 |
| `cube.list_missions` | `GET /api/cube/missions?query=&limit=` | | `Mission[]` | `list_missions` | NONE | S3 |
| `cube.find_missions` | `POST /api/cube/find` | `FindMissions` | `FoundMissions` | `find_missions` | NONE | S3 |
| `cube.get_mission` | `GET /api/cube/missions/:key?silo=&stage=&at=&knownAt=` | | `MissionState` | `get_mission` | NONE | S3 |
| `cube.compare_missions` | `POST /api/cube/compare` | `CompareMissions` | `Comparison` | `compare_missions` | NONE | S3 |
| `cube.get_value_evidence` | `GET /api/cube/values/:id` | | `ValueEvidence` | `get_value_evidence` | NONE | S3 |
| `cube.prepare_values` | `POST /api/cube/candidates` | `PrepareValues` | `Candidates` | `prepare_values` | NONE | S3 |
| `cube.similar_missions` | `POST /api/cube/similar` | `SimilarMissions` | `SimilarPage` | `similar_missions` | NONE | S3 |
| `cube.explain_similarity` | `POST /api/cube/similar/explain` | `ExplainSimilarity` | `SimilarityExplanation` | `explain_similarity` | NONE | S3 |
| `cube.submit_values` | `POST /api/cube/values` | `SubmitValues` | `ValueReceipt` | `submit_values`, pauses for approval | DATA | S4 |
| `cube.save_view` | `POST /api/cube/views` | `SaveView` | `WorkspaceFile` | `save_view`, pauses for approval | DOCUMENT | S5 |

Limits: 1 to 12 conditions and at most 20 missions in `find_missions`; 2 to 5 missions in `compare_missions`; at most 10 results in `similar_missions`; at most 100 candidates in `submit_values`; at most 200 rows in a saved view. `at` is a date (`YYYY-MM-DD`) and `knownAt` a timestamp; both default to the time of the request and are returned. Source numbers are decimal strings of at most 100 characters without exponent, NaN or Infinity.

## 13. Datacube TypeScript forms

```ts
export type Facet = "purpose" | "design" | "operations" | "program" | "cost_and_risk";
export type ValueKind = "exact" | "range" | "unknown" | "not_applicable";
export interface Parameter {
  key: string; label: string; unit: string; min: number | null; max: number | null; whole: boolean;
  units: { unit: string; multiplier: string; offset: string }[];
}
export interface Mission { key: string; title: string; currentStage: string; aliases: string[]; simulated: boolean }
export interface Condition {
  parameter: string; op: "eq" | "ge" | "le" | "between"; value: string; high?: string; unit: string;
}
export interface FindMissions {
  conditions: Condition[]; silo?: string; stage?: string;        // stage "current" = each mission's current stage
  at?: string; knownAt?: Timestamp; limit?: number;
}
export interface CubeValue {
  id: Id; mission: string; silo: string; stage: string | null; element: string | null; parameter: string;
  kind: ValueKind; low: number | null; high: number | null; unit: string;
  sourceNumber: string | null; sourceUnit: string | null;
  validFrom: string; recordedAt: Timestamp; citation: CitationHandle;
  withdrawn: boolean;                  // true: shown as unknown, source withdrawn
  approvalId: Id | null; simulated: boolean;
}
export interface FoundMissions {
  missions: { mission: Mission; matched: CubeValue[] }[]; at: string; knownAt: Timestamp; truncated: boolean;
}
export interface MissionState {
  mission: Mission; values: CubeValue[]; gaps: string[];         // parameter keys without a value
  at: string; knownAt: Timestamp;
}
export interface CompareMissions {
  keys: string[]; parameters: string[]; silo?: string; stage?: string; at?: string; knownAt?: Timestamp;
}
export interface Comparison {
  parameters: string[]; rows: { mission: Mission; values: (CubeValue | null)[] }[];
  at: string; knownAt: Timestamp;
}
export interface ValueEvidence {
  value: CubeValue; quote: string; conversion: string;
  citationStatus: "current" | "changed" | "newer_version" | "withdrawn";
}
export interface PrepareValues {
  handle: CitationHandle;              // a table chunk
  mission?: string; silo?: string; stage?: string; validFrom?: string;
}
export interface Candidate {
  row: number; header: string; parameter: string | null; element: string | null;
  sourceNumber: string; sourceUnit: string | null; kind: ValueKind;
  low: number | null; high: number | null; citation: CitationHandle; problem: string | null;
}
export interface Candidates {
  mission: string | null; silo: string | null; stage: string | null; validFrom: string | null;
  candidates: Candidate[]; problems: string[];
}
export interface SubmitValues {
  mission: string; silo: string; stage: string | null; validFrom: string;
  candidates: Candidate[];             // only candidates without a problem
}
export interface ValueReceipt { approvalId: Id; valueIds: Id[] }
export interface SimilarMissions { key: string; facets?: Facet[]; limit?: number }   // 1 to 10, default 5
export interface SimilarMission {
  mission: Mission; score: number | null; comparable: boolean;
  facetScores: Partial<Record<Facet, number | null>>;             // null: missing on one side
}
export interface SimilarPage { missions: SimilarMission[]; facets: Facet[] }
export interface ExplainSimilarity { key: string; other: string; facets?: Facet[] }
export interface SimilarityExplanation {
  facets: { facet: Facet; score: number | null; text: string | null; otherText: string | null;
            sources: string[]; otherSources: string[] }[];
}
export interface SaveView {
  conversationId: Id; name: string;
  operation: "find_missions" | "get_mission" | "compare_missions" | "similar_missions";
  input: Record<string, unknown>;      // that operation's request
}
```

## 14. Datacube route declarations

```ts
import type * as T from "./types";
export interface CubeRoutes {
  "GET /api/cube/parameters":         { response: T.Parameter[] };
  "GET /api/cube/missions":           { query: { query?: string; limit?: number }; response: T.Mission[] };
  "POST /api/cube/find":              { body: T.FindMissions; response: T.FoundMissions };
  "GET /api/cube/missions/:key":      { query: { silo?: string; stage?: string; at?: string; knownAt?: T.Timestamp };
                                        response: T.MissionState };
  "POST /api/cube/compare":           { body: T.CompareMissions; response: T.Comparison };
  "GET /api/cube/values/:id":         { response: T.ValueEvidence };
  "POST /api/cube/candidates":        { body: T.PrepareValues; response: T.Candidates };
  "POST /api/cube/similar":           { body: T.SimilarMissions; response: T.SimilarPage };
  "POST /api/cube/similar/explain":   { body: T.ExplainSimilarity; response: T.SimilarityExplanation };
  "POST /api/cube/values":            { body: T.SubmitValues; response: T.ValueReceipt };
  "POST /api/cube/views":             { body: T.SaveView; response: T.WorkspaceFile };
}
```

## 15. Synthetic datacube examples

```http
POST /api/cube/find
Content-Type: application/json

{ "conditions": [ { "parameter": "mass_dry", "op": "le", "value": "150", "unit": "kg" } ], "stage": "current", "at": "2026-10-15" }

200 OK
{ "missions": [ { "mission": { "key": "msn-sim-01", "title": "Simulated mission 01", "currentStage": "st3-design",
      "aliases": ["sim-01"], "simulated": true },
    "matched": [ { "id": "0199e7d0-5b1e-7a40-9f0e-3c1d2b8a4e61", "mission": "msn-sim-01", "silo": "engineering",
      "stage": "st3-design", "element": null, "parameter": "mass_dry", "kind": "exact", "low": 120, "high": 120,
      "unit": "kg", "sourceNumber": "120", "sourceUnit": "kg", "validFrom": "2026-03-01",
      "recordedAt": "2026-10-15T14:02:00Z",
      "citation": { "item": "itm_4f9c2a0b1c2d", "version": 2, "block": 5, "locator": "sheet=Mass;range=A1:C9;row=3",
                    "quoteSha256": "9d0e…", "simulated": true },
      "withdrawn": false, "approvalId": null, "simulated": true } ] } ],
  "at": "2026-10-15", "knownAt": "2026-10-15T15:00:00Z", "truncated": false }
```

```http
POST /api/cube/similar
Content-Type: application/json

{ "key": "msn-sim-01", "facets": ["design", "operations"] }

200 OK
{ "missions": [ { "mission": { "key": "msn-sim-04", "title": "Simulated mission 04", "currentStage": "st4-build",
      "aliases": [], "simulated": true },
    "score": 0.895, "comparable": true, "facetScores": { "design": 0.91, "operations": 0.88 } } ],
  "facets": ["design", "operations"] }
```
