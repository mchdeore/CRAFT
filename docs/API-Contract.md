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
| `GET /api/documents?query=` | | `DocumentHit[]` | readable documents | `search_documents` | S1 |
| `GET /api/documents/:id` | | `Document` | readable | `read_document` | S1 |
| `GET /api/documents/:id/preview` | | The original file, inline | readable | | S1 |
| `GET /api/documents/:id/download` | | The original file, as an attachment | readable | | S1 |
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
GET /api/documents?query=radiator%20thermal%20margin

200 OK
[ { "documentId": "0199e7c3-5b1e-7a40-9f0e-3c1d2b8a4e61", "passageId": "0199e7c3-5b1e-7a40-9f0e-3c1d2b8a4e62", "title": "Thermal Control Plan",
    "section": "4.2 Radiator margins", "snippet": "…a margin of 5 K at end of life…", "score": 0.0325, "simulated": true } ]
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
export interface DocumentHit {
  documentId: Id; passageId: Id; title: string; section: string; snippet: string; score: number;
  simulated: boolean;                  // from a simulated source
}
export interface Document {
  id: Id; title: string; source: string;
  version: string;                     // SHA-256 of the original file
  simulated: boolean; classification: Classification; sections: Section[];
}
export interface Section { id: Id; heading: string; locator: string; body: string }

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
  passageId: Id; documentId: Id; title: string; section: string;
  version: string;                     // SHA-256 of the original file
  artifact: string;                    // the file's name, for the preview route
  locator: string;                     // the page, or the section where the file has no pages
  simulated: boolean;                  // from a simulated source
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
  tool: string;                        // e.g. "search_documents"
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
  "GET /api/documents":               { query: { query: string }; response: T.DocumentHit[] };
  "GET /api/documents/:id":           { response: T.Document };
  "GET /api/documents/:id/preview":   { query: { version: string; artifact: string; locator: string };
                                        response: Blob };                  // the original, inline
  "GET /api/documents/:id/download":  { query: { version: string }; response: Blob };  // attachment
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
data: {"type":"TOOL_CALL_START","toolCallId":"call_1","toolCallName":"search_documents"}
data: {"type":"TOOL_CALL_ARGS","toolCallId":"call_1","delta":"{\"query\":\"radiator margin\"}"}
data: {"type":"TOOL_CALL_END","toolCallId":"call_1"}
data: {"type":"TOOL_CALL_RESULT","toolCallId":"call_1","messageId":"0199e7c2-5b1e-7a40-9f0e-3c1d2b8a4e70","content":"8 passages"}
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

Datacube operations use the existing access, error, request, approval and stream conventions. Section 8 of the specification defines parameter and evidence semantics. Read operations return fixed times and fully resolved scopes. Source numbers are bounded decimal strings; computed scores are finite JSON numbers. Unknown keys, units or markers return `invalid_input`; unreadable handles return `not_found`; stale versions or changed replay bodies return `conflict`.

| Function and operation namespace | HTTP route | Request | Response | Effect |
|---|---|---|---|---|
| cube.list_cube_parameters | GET /api/cube/parameters | CubeCatalogInput | CubeDictionary | NONE |
| cube.list_cube_missions | GET /api/cube/missions | CubeMissionList | CubeMissionPage | NONE |
| cube.find_cube_missions | POST /api/cube/find | CubeQuery | CubeSearch | NONE |
| cube.get_cube_mission | GET /api/cube/missions/:missionId | CubeMissionInput | CubeMission | NONE |
| cube.compare_cube_missions | POST /api/cube/compare | CubeCompareInput | CubeComparison | NONE |
| cube.similar_cube_missions | POST /api/cube/similar | CubeSimilarInput | CubeSimilarPage | NONE |
| cube.explain_cube_distance | POST /api/cube/distance | CubeDistanceInput | CubeDistance | NONE |
| cube.get_cube_value_evidence | GET /api/cube/values/:valueId | CubeValueInput | CubeEvidence | NONE |
| cube.search_cube_documents | POST /api/cube/documents/search | CubeDocumentQuery | CubeDocumentPage | NONE |
| cube.export_cube_view | POST /api/cube/views | CubeExportInput | WorkspaceFile | DOCUMENT |
| cube.submit_cube_values | POST /api/cube/values/candidates | CubeCandidateInput | CubeReceipt | DATA |
| cube.list_cube_approvals | GET /api/cube/approvals | CubeApprovalInput | CubeApprovalPage | NONE |
| cube.submit_cube_definition | POST /api/cube/definitions | CubeDefinitionInput | CubeReceipt | DATA |

GET path fields come from the route. The strict nested `scope` query field is percent-encoded JSON; POST uses nested JSON. Queries have 1 to 12 AND predicates and at most 20 missions. Comparisons contain 2 to 5 distinct missions; intake has at most 100 candidates; definitions have 2 to 100 reference handles and 1 to 12 dimensions. Compact results are at most 16 kibibytes (KiB); detailed results at most 64 KiB. Truncation preserves whole items and handles. Numeric source strings contain at most 100 characters and use signed decimal notation without exponent, NaN or Infinity. Nominal equality accepts a bounded category string.

Reads have effect NONE. Values and definitions have effect DATA; the bounded JSON workspace view has effect DOCUMENT. The three writes use an exact request identifier and arguments hash, requester approval and one execution. The view stays with its conversation, survives sign-out and is deleted on archive. External transfer uses the existing export gate. Effectful route results shown below are approval receipts or the completed result after approval; no example authorizes an unapproved write.

A scoped GET request encodes the strict JSON query field as follows. Its complete result appears in section 15.4.

```http
GET /api/cube/missions/10000000-0000-0000-0000-000000000001?scope=%7B%22siloId%22%3A%22engineering%22%2C%22stageId%22%3A%22concept%22%2C%22simulated%22%3Atrue%2C%22validAt%22%3A%222026-01-15T00%3A00%3A00Z%22%2C%22knownAt%22%3A%222026-01-15T00%3A00%3A00Z%22%7D
```

## 13. Datacube TypeScript forms

Optional input fields receive the Python defaults. Resolved output scopes contain every default and both UTC times. Nullable output fields are present.

```ts
export interface CubeResolvedScope { siloId:string; stageId:string; classId:string; optionId:Id|null; simulated:boolean; validAt:Timestamp; knownAt:Timestamp; mode:'compact'|'detailed' }
export interface CubeScope { siloId:string; stageId:string; classId?:string; optionId?:Id|null; simulated?:boolean; validAt?:Timestamp|null; knownAt?:Timestamp|null; mode?:'compact'|'detailed' }
export interface CubeCatalogInput { category?:string|null; limit?:number }
export interface CubeUnit { id:string; dimension:string; multiplier:string; offsetValue:string }
export interface CubeParameter { id:string; label:string; dimension:string; unitId:string; scale:'linear'|'log'|'ordinal'|'nominal'; minimum:string|null; maximum:string|null; synonyms:string[] }
export interface CubeDictionary { parameters:CubeParameter[]; units:CubeUnit[]; classIds:string[]; siloIds:string[]; stageIds:string[]; truncated:boolean }
export interface CubeMissionList { simulated?:boolean; limit?:number }
export interface CubeMissionSummary { id:Id; key:string; title:string; simulated:boolean; createdAt:Timestamp }
export interface CubeMissionPage { items:CubeMissionSummary[]; returned:number; truncated:boolean }
export interface CubePredicate { parameterId:string; operator:'eq'|'ge'|'le'|'between'; value:string; unitId:string; upperValue?:string|null }
export interface CubeQuery { scope:CubeScope; predicates:CubePredicate[]; missionIds?:Id[]; limit?:number }
export interface CubeCell { valueId:Id; missionId:Id; elementId:Id; parameterId:string; unitId:string; numericValue:string|null; textValue:string|null; lowerBound:string|null; upperBound:string|null; marker:'exact'|'range'|'lower'|'upper'|'tbd'|'not-applicable'; basis:'observed'|'assumed'|'imputed'|'derived'; confidence:number; simulated:boolean; evidenceHandle:Id }
export interface CubeMatch { mission:CubeMissionSummary; cells:CubeCell[]; gaps:string[] }
export interface CubeSearch { items:CubeMatch[]; scope:CubeResolvedScope; returned:number; truncated:boolean }
export interface CubeMissionInput { missionId:Id; scope:CubeScope }
export interface CubeMission { mission:CubeMissionSummary; scope:CubeResolvedScope; cells:CubeCell[]; documentIds:Id[]; gaps:string[]; truncated:boolean }
export interface CubeCompareInput { missionIds:Id[]; parameterIds:string[]; scope:CubeScope }
export interface CubeDelta { leftValueId:Id; rightValueId:Id; parameterId:string; delta:string; unitId:string }
export interface CubeComparison { missions:CubeMatch[]; deltas:CubeDelta[]; scope:CubeResolvedScope; truncated:boolean }
export interface CubeSimilarInput { missionId:Id; definitionId:Id; scope:CubeScope; limit?:number }
export interface CubeDistanceInput { leftMissionId:Id; rightMissionId:Id; definitionId:Id; scope:CubeScope }
export interface CubeContribution { parameterId:string; leftValueId:Id; rightValueId:Id; weight:number; sigma:string; squared:number; share:number }
export interface CubeDistance { leftMissionId:Id; rightMissionId:Id; definitionId:Id; definitionVersion:number; comparable:boolean; distance:number|null; coverage:number; contributions:CubeContribution[]; skipped:string[]; scope:CubeResolvedScope; truncated:boolean }
export interface CubeSimilarPage { items:CubeDistance[]; returned:number; truncated:boolean }
export interface CubeValueInput { valueId:Id }
export interface CubeEvidence { cell:CubeCell; citation:Citation; recipe:string; validFrom:Timestamp; validTo:Timestamp|null; recordedAt:Timestamp; inputIds:Id[]; truncated:boolean }
export interface CubeDocumentQuery { missionId:Id; scope:CubeScope; query:string; limit?:number }
export interface CubeDocumentPage { citations:Citation[]; scores:number[]; scope:CubeResolvedScope; truncated:boolean }
export interface CubeExportInput { conversationId:Id; requestId:Id; query:CubeQuery }
export interface CubeCandidate { elementId:Id; parameterId:string; documentId:Id; version:string; locator:string; sourceUnitId:string; numericValue:string|null; textValue:string|null; lowerBound:string|null; upperBound:string|null; marker:'exact'|'range'|'lower'|'upper'|'tbd'|'not-applicable'; basis:'observed'|'assumed'; confidence:number; recipe:string; validFrom:Timestamp; validTo:Timestamp|null }
export interface CubeCandidateInput { missionId:Id; scope:CubeScope; conversationId:Id; requestId:Id; candidates:CubeCandidate[] }
export interface CubeReceipt { approvalRequestId:Id; status:'pending'|'executed'; valueIds:Id[]; definitionId:Id|null }
export interface CubeApprovalInput { conversationId:Id; status?:'pending'|'approved'|'rejected'|'executed'|'failed' }
export interface CubeApprovalPage { items:Approval[]; truncated:boolean }
export interface CubeDimension { parameterId:string; weight:number; sigma:string }
export interface CubeDefinitionInput { key:string; version:number; conversationId:Id; requestId:Id; simulated:boolean; referenceValueIds:Id[]; dimensions:CubeDimension[]; minimumCoverage:number; cutoff:number; resultLimit?:number }
```

## 14. Datacube route declarations

```ts
import type * as T from "./types";
export interface CubeRoutes {
  "GET /api/cube/parameters": { query: T.CubeCatalogInput; response: T.CubeDictionary };
  "GET /api/cube/missions": { query: T.CubeMissionList; response: T.CubeMissionPage };
  "POST /api/cube/find": { body: T.CubeQuery; response: T.CubeSearch };
  "GET /api/cube/missions/:missionId": { path: { missionId: T.Id }; query: { scope: T.CubeScope }; response: T.CubeMission };
  "POST /api/cube/compare": { body: T.CubeCompareInput; response: T.CubeComparison };
  "POST /api/cube/similar": { body: T.CubeSimilarInput; response: T.CubeSimilarPage };
  "POST /api/cube/distance": { body: T.CubeDistanceInput; response: T.CubeDistance };
  "GET /api/cube/values/:valueId": { path: { valueId: T.Id }; response: T.CubeEvidence };
  "POST /api/cube/documents/search": { body: T.CubeDocumentQuery; response: T.CubeDocumentPage };
  "POST /api/cube/views": { body: T.CubeExportInput; response: T.WorkspaceFile };
  "POST /api/cube/values/candidates": { body: T.CubeCandidateInput; response: T.CubeReceipt };
  "GET /api/cube/approvals": { query: T.CubeApprovalInput; response: T.CubeApprovalPage };
  "POST /api/cube/definitions": { body: T.CubeDefinitionInput; response: T.CubeReceipt };
}
```

## 15. Synthetic datacube examples

These are made-up wire objects for contract implementation. GET request objects separate path and query fields; `scope` must be JSON-encoded in the URL query.

### 15.1 list_cube_parameters

Request:

```json
{"category":"mass","limit":100}
```

Response:

```json
{"parameters":[{"id":"mass","label":"Mass","dimension":"mass","unitId":"kg","scale":"linear","minimum":"0","maximum":null,"synonyms":["mass","masse"]}],"units":[{"id":"kg","dimension":"mass","multiplier":"1","offsetValue":"0"},{"id":"g","dimension":"mass","multiplier":"0.001","offsetValue":"0"}],"classIds":["system"],"siloIds":["engineering"],"stageIds":["concept"],"truncated":false}
```

### 15.2 list_cube_missions

Request:

```json
{"simulated":true,"limit":20}
```

Response:

```json
{"items":[{"id":"10000000-0000-0000-0000-000000000001","key":"alpha","title":"Synthetic Alpha","simulated":true,"createdAt":"2026-01-01T00:00:00Z"},{"id":"10000000-0000-0000-0000-000000000002","key":"beta","title":"Synthetic Beta","simulated":true,"createdAt":"2026-01-01T00:00:00Z"}],"returned":2,"truncated":false}
```

### 15.3 find_cube_missions

Request:

```json
{"scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"},"predicates":[{"parameterId":"mass","operator":"le","value":"105","unitId":"kg","upperValue":null}],"missionIds":[],"limit":20}
```

Response:

```json
{"items":[{"mission":{"id":"10000000-0000-0000-0000-000000000001","key":"alpha","title":"Synthetic Alpha","simulated":true,"createdAt":"2026-01-01T00:00:00Z"},"cells":[{"valueId":"50000000-0000-0000-0000-000000000001","missionId":"10000000-0000-0000-0000-000000000001","elementId":"40000000-0000-0000-0000-000000000001","parameterId":"mass","unitId":"kg","numericValue":"100","textValue":null,"lowerBound":null,"upperBound":null,"marker":"exact","basis":"observed","confidence":1,"simulated":true,"evidenceHandle":"50000000-0000-0000-0000-000000000001"}],"gaps":[]}],"scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"},"returned":1,"truncated":false}
```

### 15.4 get_cube_mission

Request:

```json
{"missionId":"10000000-0000-0000-0000-000000000001","scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"}}
```

Response:

```json
{"mission":{"id":"10000000-0000-0000-0000-000000000001","key":"alpha","title":"Synthetic Alpha","simulated":true,"createdAt":"2026-01-01T00:00:00Z"},"scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"},"cells":[{"valueId":"50000000-0000-0000-0000-000000000001","missionId":"10000000-0000-0000-0000-000000000001","elementId":"40000000-0000-0000-0000-000000000001","parameterId":"mass","unitId":"kg","numericValue":"100","textValue":null,"lowerBound":null,"upperBound":null,"marker":"exact","basis":"observed","confidence":1,"simulated":true,"evidenceHandle":"50000000-0000-0000-0000-000000000001"},{"valueId":"50000000-0000-0000-0000-000000000002","missionId":"10000000-0000-0000-0000-000000000001","elementId":"40000000-0000-0000-0000-000000000001","parameterId":"power","unitId":"W","numericValue":"200","textValue":null,"lowerBound":null,"upperBound":null,"marker":"exact","basis":"observed","confidence":1,"simulated":true,"evidenceHandle":"50000000-0000-0000-0000-000000000002"}],"documentIds":["20000000-0000-0000-0000-000000000001"],"gaps":[],"truncated":false}
```

### 15.5 compare_cube_missions

Request:

```json
{"missionIds":["10000000-0000-0000-0000-000000000001","10000000-0000-0000-0000-000000000002"],"parameterIds":["mass"],"scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"}}
```

Response:

```json
{"missions":[{"mission":{"id":"10000000-0000-0000-0000-000000000001","key":"alpha","title":"Synthetic Alpha","simulated":true,"createdAt":"2026-01-01T00:00:00Z"},"cells":[{"valueId":"50000000-0000-0000-0000-000000000001","missionId":"10000000-0000-0000-0000-000000000001","elementId":"40000000-0000-0000-0000-000000000001","parameterId":"mass","unitId":"kg","numericValue":"100","textValue":null,"lowerBound":null,"upperBound":null,"marker":"exact","basis":"observed","confidence":1,"simulated":true,"evidenceHandle":"50000000-0000-0000-0000-000000000001"}],"gaps":[]},{"mission":{"id":"10000000-0000-0000-0000-000000000002","key":"beta","title":"Synthetic Beta","simulated":true,"createdAt":"2026-01-01T00:00:00Z"},"cells":[{"valueId":"50000000-0000-0000-0000-000000000003","missionId":"10000000-0000-0000-0000-000000000002","elementId":"40000000-0000-0000-0000-000000000002","parameterId":"mass","unitId":"kg","numericValue":"110","textValue":null,"lowerBound":null,"upperBound":null,"marker":"exact","basis":"observed","confidence":1,"simulated":true,"evidenceHandle":"50000000-0000-0000-0000-000000000003"}],"gaps":[]}],"deltas":[{"leftValueId":"50000000-0000-0000-0000-000000000001","rightValueId":"50000000-0000-0000-0000-000000000003","parameterId":"mass","delta":"10","unitId":"kg"}],"scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"},"truncated":false}
```

### 15.6 similar_cube_missions

Request:

```json
{"missionId":"10000000-0000-0000-0000-000000000001","definitionId":"60000000-0000-0000-0000-000000000001","scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"},"limit":5}
```

Response:

```json
{"items":[{"leftMissionId":"10000000-0000-0000-0000-000000000001","rightMissionId":"10000000-0000-0000-0000-000000000002","definitionId":"60000000-0000-0000-0000-000000000001","definitionVersion":1,"comparable":true,"distance":1,"coverage":1,"contributions":[{"parameterId":"mass","leftValueId":"50000000-0000-0000-0000-000000000001","rightValueId":"50000000-0000-0000-0000-000000000003","weight":0.6,"sigma":"10","squared":0.6,"share":0.6},{"parameterId":"power","leftValueId":"50000000-0000-0000-0000-000000000002","rightValueId":"50000000-0000-0000-0000-000000000004","weight":0.4,"sigma":"20","squared":0.4,"share":0.4}],"skipped":[],"scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"},"truncated":false}],"returned":1,"truncated":false}
```

### 15.7 explain_cube_distance

Request:

```json
{"leftMissionId":"10000000-0000-0000-0000-000000000001","rightMissionId":"10000000-0000-0000-0000-000000000002","definitionId":"60000000-0000-0000-0000-000000000001","scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"}}
```

Response:

```json
{"leftMissionId":"10000000-0000-0000-0000-000000000001","rightMissionId":"10000000-0000-0000-0000-000000000002","definitionId":"60000000-0000-0000-0000-000000000001","definitionVersion":1,"comparable":true,"distance":1,"coverage":1,"contributions":[{"parameterId":"mass","leftValueId":"50000000-0000-0000-0000-000000000001","rightValueId":"50000000-0000-0000-0000-000000000003","weight":0.6,"sigma":"10","squared":0.6,"share":0.6},{"parameterId":"power","leftValueId":"50000000-0000-0000-0000-000000000002","rightValueId":"50000000-0000-0000-0000-000000000004","weight":0.4,"sigma":"20","squared":0.4,"share":0.4}],"skipped":[],"scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"},"truncated":false}
```

### 15.8 get_cube_value_evidence

Request:

```json
{"valueId":"50000000-0000-0000-0000-000000000001"}
```

Response:

```json
{"cell":{"valueId":"50000000-0000-0000-0000-000000000001","missionId":"10000000-0000-0000-0000-000000000001","elementId":"40000000-0000-0000-0000-000000000001","parameterId":"mass","unitId":"kg","numericValue":"100","textValue":null,"lowerBound":null,"upperBound":null,"marker":"exact","basis":"observed","confidence":1,"simulated":true,"evidenceHandle":"50000000-0000-0000-0000-000000000001"},"citation":{"passageId":"a0000000-0000-0000-0000-000000000001","documentId":"20000000-0000-0000-0000-000000000001","title":"Synthetic parameters","section":"Parameters","version":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","artifact":"parameters.xlsx","locator":"sheet:parameters!B2","simulated":true,"classification":"unclassified","quote":"Mass: 100 kg"},"recipe":"table-v1","validFrom":"2026-01-01T00:00:00Z","validTo":null,"recordedAt":"2026-01-02T00:00:00Z","inputIds":[],"truncated":false}
```

### 15.9 search_cube_documents

Request:

```json
{"missionId":"10000000-0000-0000-0000-000000000001","scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"},"query":"mass","limit":8}
```

Response:

```json
{"citations":[{"passageId":"a0000000-0000-0000-0000-000000000001","documentId":"20000000-0000-0000-0000-000000000001","title":"Synthetic parameters","section":"Parameters","version":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","artifact":"parameters.xlsx","locator":"sheet:parameters!B2","simulated":true,"classification":"unclassified","quote":"Mass: 100 kg"}],"scores":[1],"scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"},"truncated":false}
```

### 15.10 export_cube_view

Request:

```json
{"conversationId":"70000000-0000-0000-0000-000000000001","requestId":"80000000-0000-0000-0000-000000000001","query":{"scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"},"predicates":[{"parameterId":"mass","operator":"le","value":"105","unitId":"kg","upperValue":null}],"missionIds":[],"limit":20}}
```

Response:

```json
{"id":"b0000000-0000-0000-0000-000000000001","conversationId":"70000000-0000-0000-0000-000000000001","name":"view.json","mediaType":"application/json","sizeBytes":1024,"origin":"agent","state":"ready","simulated":true,"classification":"unclassified","createdAt":"2026-01-15T00:00:00Z"}
```

### 15.11 submit_cube_values

Request:

```json
{"missionId":"10000000-0000-0000-0000-000000000001","scope":{"siloId":"engineering","stageId":"concept","classId":"system","optionId":null,"simulated":true,"validAt":"2026-01-15T00:00:00Z","knownAt":"2026-01-15T00:00:00Z","mode":"compact"},"conversationId":"70000000-0000-0000-0000-000000000001","requestId":"80000000-0000-0000-0000-000000000001","candidates":[{"elementId":"40000000-0000-0000-0000-000000000001","parameterId":"mass","documentId":"20000000-0000-0000-0000-000000000001","version":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","locator":"sheet:parameters!B2","sourceUnitId":"kg","numericValue":"100","textValue":null,"lowerBound":null,"upperBound":null,"marker":"exact","basis":"observed","confidence":1,"recipe":"table-v1","validFrom":"2026-01-01T00:00:00Z","validTo":null}]}
```

Response:

```json
{"approvalRequestId":"90000000-0000-0000-0000-000000000001","status":"pending","valueIds":[],"definitionId":null}
```

### 15.12 list_cube_approvals

Request:

```json
{"conversationId":"70000000-0000-0000-0000-000000000001","status":"pending"}
```

Response:

```json
{"items":[{"id":"90000000-0000-0000-0000-000000000001","conversationId":"70000000-0000-0000-0000-000000000001","runId":"c0000000-0000-0000-0000-000000000001","tool":"submit_cube_values","summary":"One candidate value","status":"pending","requestedAt":"2026-01-15T00:00:00Z"}],"truncated":false}
```

### 15.13 submit_cube_definition

Request:

```json
{"key":"technical","version":2,"conversationId":"70000000-0000-0000-0000-000000000001","requestId":"80000000-0000-0000-0000-000000000001","simulated":true,"referenceValueIds":["50000000-0000-0000-0000-000000000001","50000000-0000-0000-0000-000000000007","50000000-0000-0000-0000-000000000002","50000000-0000-0000-0000-000000000009"],"dimensions":[{"parameterId":"mass","weight":0.6,"sigma":"10"},{"parameterId":"power","weight":0.4,"sigma":"20"}],"minimumCoverage":0.6,"cutoff":100,"resultLimit":5}
```

Response:

```json
{"approvalRequestId":"90000000-0000-0000-0000-000000000001","status":"pending","valueIds":[],"definitionId":null}
```
