# API contract

The HTTP interface between the front end and the backend. The executable contract is the code in [`app/contract/`](https://github.com/mchdeore/CRAFT/tree/main/app/contract); this page holds the rules, the route and tool tables and the example index.

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP) with JavaScript Object Notation (JSON). Agent-User Interaction (AG-UI) events use Server-Sent Events (SSE). Identifiers are universally unique identifiers (UUIDs). Times follow the International Organization for Standardization (ISO) 8601 format in Coordinated Universal Time (UTC).

## 1. Rules

1. `app/contract/types.ts` and `app/contract/routes.ts` define the built forms. Table 5.2 lists agreed forms that are not built yet.
2. Ordinary routes use one HTTP request and one response. File routes return bytes.
3. The chat route takes AG-UI `RunAgentInput` and returns AG-UI events as SSE.
4. A contract change updates `app/contract/`, the backend and the contract checks together.
5. Table 5.1 describes the app. Table 5.2 preserves the remaining agreements and their build steps.

## 2. Conventions

| Item | Rule |
|---|---|
| Prefix | `/api`. Health checks are outside it. There is no generic run-any-operation route. |
| Format | JSON, `camelCase` field names, UUID identifiers, ISO 8601 UTC times |
| Sign-in | Cookie `__Host-craft_session` (`Secure`, `HttpOnly`, `SameSite=Lax` by default, `Path=/`), set at the end of the identity provider round trip (`GET /api/auth/login`, then `GET /api/auth/callback`). Send requests with `credentials: "include"`. No tokens in browser storage |
| Workspace token | `Authorization: Bearer <token>` from `POST /api/me/workspace-tokens`, shown once, 1 to 168 hours (default 12). It signs in as its account on the read routes whose operation is a read tool (section 6) and on `GET /api/me`; every other route returns `403`. A request with both a token and a session cookie returns `401`. Calls are logged with origin `workspace` and the token's identifier. Revoked, expired or disabled-account tokens return `401` |
| Writes | Ordinary `/api` requests using `POST`, `PUT`, `PATCH` and `DELETE` must carry an `Origin` header from the allowed list |
| Request ID | Every response has `X-Request-Id`. The same value appears in error bodies and server logs |
| Unreadable records | `404`, the same as a missing record |
| Caching | Operation responses under `/api` use `Cache-Control: no-store`, including authentication, errors, files and SSE streams |
| Lists | Operation-specific limits; no paging. The conversation list agreement is newest first, at most 100 items |
| Files | Sent with their media type and `X-Content-Type-Options: nosniff`. Preview is inline only for PDF, images and plain text, under `Content-Security-Policy: sandbox`; other types download. Upload: raw bytes, the name in `X-File-Name` |

Cross-origin browser access is opt-in. Set `CRAFT_CROSS_ORIGIN_BROWSER=true` and list exact HTTPS origins in `CRAFT_ALLOWED_ORIGINS`. This enables credentialed CORS and `SameSite=None` cookies while retaining the write Origin check and session protections. Preflights support the existing API methods and upload headers and receive a request ID. Some browsers block cross-site cookies; use a frontend served from the API origin when required. These settings alone do not deploy a public application.

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
| Document catalog | Default 25, limit 1 to 50; scan at most 200 candidate source IDs; explicit truncation |
| Approval decision note | Optional or null; text at most 2,000 characters |
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
| `GET /api/notebook-grants` | —; query `{ conversationId?: Id }` | `NotebookGrant[]` | signed in; requester ownership | — |
| `DELETE /api/notebook-grants/:id` | — | `void` | signed in; requester ownership | — |
| `GET /api/tools` | — | `ToolInfo[]` | signed in; live resource access | — |
| `GET /api/tools/capabilities` | — | `CapabilityCatalog` | signed in | `list_capabilities` |
| `GET /api/library/documents` | —; query `DocumentList` | `DocumentCatalog` | signed in; current readable indexed sources | `list_documents` |
| `POST /api/agent/runs` | `RunAgentInput` | `AG-UI event stream` | signed in; live resource access | — |
| `GET /api/agent/runs/:run/events` | —; query `{ after?: number }` | `AG-UI event stream` | signed in; owned run | — |
| `POST /api/agent/runs/:run/cancel` | — | `void` | signed in; owned run | — |
| `POST /api/cube/candidates` | `PrepareValues` | `Candidates` | signed in; live resource access | `prepare_values` |
| `POST /api/cube/values` | `SubmitValues` | `ValueReceipt` | signed in; requester-approved batch | `submit_values` |
| `POST /api/library/citations/check` | `CitationCheck` | `CitationStatus` | signed in; live resource access | `check_citation` |
| `GET /api/library/documents/:item/compare` | —; query `{ fromVersion: number; toVersion: number }` | `VersionDiff` | signed in; live resource access | `compare_versions` |
| `GET /health/live` | — | `Health` | public | — |
| `GET /health/ready` | — | `Health` | public | — |
| `GET /health/queue` | — | `QueueHealth` | public | — |
| `GET /api/jobs` | —; query `JobPageRequest` | `JobPage` | signed in; own jobs (administrator: all) | — |
| `GET /api/jobs/:jobId` | — | `Job` | signed in; own job (administrator: any) | — |
| `POST /api/jobs/:jobId/retry` | — | `Job` | signed in; own job (administrator: any) | — |
| `POST /api/jobs/:jobId/cancel` | — | `Job` | signed in; own job (administrator: any) | — |
| `POST /api/library/search` | `LibrarySearch` | `LibraryHits` | signed in; live resource access | `search` |
| `GET /api/library/documents/:item` | —; query `{ version?: number }` | `LibraryDocument` | signed in; live resource access | `get_document` |
| `GET /api/library/documents/:item/chunks` | —; query `{ version: number; fromBlock: number; toBlock: number }` | `ChunkPage` | signed in; live resource access | `read_chunks` |
| `POST /api/library/explore` | `ExploreInput` | `ChunkPage` | signed in; live resource access | `explore` |
| `GET /api/library/documents/:item/preview` | —; query `{ version: number; locator?: string }` | `Blob` | signed in; live resource access | — |
| `GET /api/library/documents/:item/download` | —; query `{ version: number; locator?: string }` | `Blob` | signed in; live resource access | — |
| `POST /api/library/documents/:item/revisions` | `Blob`; query `{ version: number }` | `Revision` | signed in; live resource access | — |
| `GET /api/library/documents/:item/revisions` | — | `Revision[]` | signed in; live resource access | — |
| `GET /api/library/documents/:item/revisions/:revision` | — | `Revision` | signed in; live resource access | — |
| `GET /api/library/documents/:item/revisions/:revision/content` | — | `Blob` | signed in; live resource access | — |
| `GET /api/auth/login` | `returnTo?` (query) | `302` to the identity provider | public | — |
| `GET /api/auth/callback` | the provider's `code`, `state` (query) | `302` to `returnTo`, or to `/?signIn=failed` or `/?signIn=no-access` | public | — |
| `POST /api/auth/logout` | — | `SignedOut` | signed in | — |
| `POST /api/session` | `SignIn` | `Me` | public; tests only (`CRAFT_LOCAL_PASSWORD_SIGNIN`) | — |
| `DELETE /api/session` | — | `void` | signed in; live resource access | — |
| `GET /api/me` | — | `Me` | signed in; live resource access | — |
| `PATCH /api/me` | `MeChange` | `Me` | signed-in browser session or command line; the caller's own row only | — |
| `POST /api/me/workspace-tokens` | `NewWorkspaceToken` | `IssuedWorkspaceToken` | signed-in browser session or command line | — |
| `GET /api/me/workspace-tokens` | — | `WorkspaceToken[]` | signed-in browser session or command line | — |
| `DELETE /api/me/workspace-tokens/:id` | — | `void` | signed-in browser session or command line | — |
| `GET /api/workspace` | — | `Workspace` | signed in; own workspace | — |
| `POST /api/workspace/start` | — | `Workspace` | signed in; own workspace; returns at once while the Hub starts it | — |
| `POST /api/workspace/stop` | — | `Workspace` | signed in; own workspace; files are kept | — |
| `POST /api/workspace/run` | `RunCode` | `CodeResult` | signed in; own workspace; requester approval when called by the agent | `run_code` |
| `PUT /api/workspace/files` | `WriteFile` | `SavedFile` | signed in; own workspace; requester approval when called by the agent | `write_file` |
| `POST /api/workspace/notebooks/edit` | `EditNotebook` | `EditedNotebook` | signed in; own workspace; requester approval when called by the agent | `edit_notebook` |
| `POST /api/workspace/charts` | `SaveChart` | `SavedChart` | signed in; own workspace; requester approval when called by the agent | `save_chart` |
| `POST /api/workspace/notebooks/run` | `GrantedRun` | `CodeResult` | signed in; own workspace; a notebook grant when called by the agent | `run_in_granted_notebook` |
| `POST /api/workspace/notebooks/edit-granted` | `EditNotebook` | `EditedNotebook` | signed in; own workspace; a notebook grant when called by the agent | `edit_granted_notebook` |
| `POST /api/workspace/notebooks/runs` | `RunNotebook` | `NotebookRunQueued` | signed in; own workspace; requester approval when called by the agent; at most two at once | `run_notebook` |
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
| `GET /api/conversations/:id/run` | — | `RunRecovery` | signed in; active owned conversation | — |
| `PATCH /api/conversations/:id` | `ConversationUpdate` | `ConversationSummary` | signed in; owned conversation | — |
| `GET /api/conversations/:id/files` | — | `WorkspaceFile[]` | signed in; owned conversation | — |
| `POST /api/conversations/:id/files` | `Blob`; query `{ classification?: T.Classification }` | `WorkspaceFile` | signed in; owned conversation | — |
| `GET /api/conversations/:id/files/:fileId/content` | — | `Blob` | signed in; owned conversation | — |
| `DELETE /api/conversations/:id/files/:fileId` | — | `void` | signed in; owned conversation | — |
| `PUT /api/notes` | `WriteNote` | `Note` | signed in; live resource access | `write_note` |
| `GET /api/notes` | —; query `{ conversationId?: string }` | `NoteSummary[]` | signed in; live resource access | `list_notes` |
| `GET /api/notes/:id` | — | `Note` | signed in; live resource access | `read_note` |
| `DELETE /api/notes/:id` | — | `void` | signed in; live resource access | `delete_note` |
| `PUT /api/skills/:id` | `SaveSkill` without path `id` | `Skill` | signed in; owned definition | — |
| `GET /api/skills` | query `SkillList` | `SkillCatalog` | signed in; owned definitions | `list_skills` |
| `GET /api/skills/:id` | path `SkillRef` | `Skill` | signed in; owned definition | — |
| `GET /api/skills/:id/versions` | path `SkillRef`; required query `revision` | `Skill` | signed in; owned revision | `read_skill_version` |
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
| `POST /api/cube/views` | `SaveView` | `WorkspaceFile` | signed in; `save_view`, DOCUMENT, approval | B27 |

Document catalogs recheck live provider grants and indexed current version hashes before returning titles. Catalogs contain safe source metadata and do not expose arbitrary database rows. Capability catalogs list current read tools and owned saved-skill names.

## 6. Agent tools

Input types include path fields; ordinary GET routes carry the other fields as query parameters. Every tool is also an ordinary route and a checked Python operation. The runtime calls them through native HTTP MCP. Read tools declare `NONE`. Note changes and value submission declare `DATA`; workspace code runs and file changes declare `EXTERNAL`. Both require requester approval when called by the agent. An approved `EXTERNAL` call is recorded as executed when it is dispatched, once; its outcome is in the call log.

| Tool | Route | Input type | Effects |
|---|---|---|---|
| `ask_agent` | `POST /api/agent/ask` | `AskAgent` | `NONE` |
| `check_calculation` | `POST /api/tools/calculation-check` | `CalculationInput` | `NONE` |
| `check_citation` | `POST /api/library/citations/check` | `CitationCheck` | `NONE` |
| `compare_missions` | `POST /api/cube/compare` | `CompareMissions` | `NONE` |
| `compare_versions` | `GET /api/library/documents/:item/compare` | `CompareVersions` | `NONE` |
| `delete_note` | `DELETE /api/notes/:id` | `NoteRef` | `DATA` |
| `edit_notebook` | `POST /api/workspace/notebooks/edit` | `EditNotebook` | `EXTERNAL` |
| `edit_granted_notebook` | `POST /api/workspace/notebooks/edit-granted` | `EditNotebook` | `EXTERNAL` |
| `explain_similarity` | `POST /api/cube/similar/explain` | `ExplainSimilarity` | `NONE` |
| `explore` | `POST /api/library/explore` | `ExploreInput` | `NONE` |
| `find_missions` | `POST /api/cube/find` | `FindMissions` | `NONE` |
| `get_document` | `GET /api/library/documents/:item` | `DocumentRef` | `NONE` |
| `get_mission` | `GET /api/cube/missions/:key` | `MissionRef` | `NONE` |
| `get_value_evidence` | `GET /api/cube/values/:id` | `ValueRef` | `NONE` |
| `list_missions` | `GET /api/cube/missions` | `ListMissions` | `NONE` |
| `list_notes` | `GET /api/notes` | `NoteList` | `NONE` |
| `list_parameters` | `GET /api/cube/parameters` | `none` | `NONE` |
| `list_skills` | `GET /api/skills` | `SkillList` | `NONE` |
| `list_capabilities` | `GET /api/tools/capabilities` | — | `NONE` |
| `list_documents` | `GET /api/library/documents` | `DocumentList` | `NONE` |
| `make_chart` | `POST /api/tools/chart` | `ChartInput` | `NONE` |
| `prepare_values` | `POST /api/cube/candidates` | `PrepareValues` | `NONE` |
| `submit_values` | `POST /api/cube/values` | `SubmitValues` | `DATA` |
| `read_chunks` | `GET /api/library/documents/:item/chunks` | `ChunkRange` | `NONE` |
| `read_note` | `GET /api/notes/:id` | `NoteRef` | `NONE` |
| `read_skill_version` | `GET /api/skills/:id/versions` | `SkillVersionRef` | `NONE` |
| `run_code` | `POST /api/workspace/run` | `RunCode` | `EXTERNAL` |
| `save_chart` | `POST /api/workspace/charts` | `SaveChart` | `EXTERNAL` |
| `run_in_granted_notebook` | `POST /api/workspace/notebooks/run` | `GrantedRun` | `EXTERNAL` |
| `run_notebook` | `POST /api/workspace/notebooks/runs` | `RunNotebook` | `EXTERNAL` |
| `search` | `POST /api/library/search` | `LibrarySearch` | `NONE` |
| `similar_missions` | `POST /api/cube/similar` | `SimilarMissions` | `NONE` |
| `write_file` | `PUT /api/workspace/files` | `WriteFile` | `EXTERNAL` |
| `write_note` | `PUT /api/notes` | `WriteNote` | `DATA` |

`/mcp` is a stateless JSON MCP mount outside the ordinary route map. It reuses the same registry server as standard input and output and lists every `tool=True` operation. Root runs offer read tools and approval-required writes; the single-call query helper offers read tools only. Private handlers and observations are absent. Unapproved or unsupported effectful calls return `approval_required`; a run credential alone grants no approval.

Authentication requires `Authorization: Bearer` with the current run credential. Missing, malformed, unknown, expired or revoked credentials return HTTP 401 and `WWW-Authenticate: Bearer` before a protocol tool executes. A browser cookie alone is refused. Tool errors carry safe code/message objects in structured and text content. `craft/toolCallId` metadata links the model call; identity and parent context load from server-owned state. Raw credentials stay outside browser storage and model arguments. The loopback target and lifetime follow [the agent page](agent.md#tools-over-mcp).

### Tool reference

Signed-in users can fetch `GET /api/tools` to inspect registered tools. Each entry includes its description, route, approval metadata, input/output JSON schemas and `availableIn` channels: `api`, `mcp`, `chat` and `read_only_query`. These channels describe tool membership; access checks and service configuration still apply.

`outputSchema` describes the native JSON result. HTTP returns 204 for no result. MCP sends JSON text and wraps list results in `structuredContent.items`; a null result has empty structured content. Raw responses and streams have `outputSchema: null`. The input schema is shared with MCP.

`GET /api/tools/capabilities` returns read tools and an overview of up to 100 owned skill names. `skillsStatus: "installed"` indicates native skill loading is supported. Use the paged Skills API for complete discovery; a catalog entry grants no source access or execution approval.

### Saved skills

The Skills API keeps private definitions independently of conversations. Each skill has a stable UUID and immutable numbered revisions. A definition contains a name, description, instructions, example inputs and registered tool names.

Save with `PUT /api/skills/:id`. A new skill uses `expectedRevision: 0`; an edit uses the opened revision. Send a new `mutationId` for each edit and reuse it when retrying the same request. An identical retry returns its original saved revision. A stale edit or changed request under the same mutation ID returns `409 conflict`.

`GET /api/skills` pages through current definitions, ordered by UUID. Use `limit` (1–100, default 50) and the returned `nextCursor` as `after`. `GET /api/skills/:id` opens the latest revision; use `/api/skills/:id/versions?revision=1` to read a specific saved definition.

New saves accept distinct registered tool names. Reads retain the original references and list removed names in `unavailableTools`. Unowned and missing definitions return `404`. Saving retains the definition without executing its instructions.

To use a saved workflow, discover it with `list_skills` and load its exact ID/revision with `read_skill_version`. A new chat run can select a definition with `skill: {"id": "…", "revision": 1}`. The native agent receives that definition and calls registered tools; effectful calls keep the requester approval gate. Removed or channel-excluded references prevent selection.

Recovery retains selected revisions and eligible revisions read through MCP. An intervening edit cannot replace a paused run's definition. A later run can select the new revision; an unselected new run starts with no saved skills. Run-linked `craft.skills` history records immutable references even when a run pauses without an answer.

## 7. Chat stream

The chat route accepts AG-UI's `RunAgentInput`. `threadId` identifies the conversation. A new `runId` is a UUID echoed in `RUN_STARTED`; reuse without resume returns `409`. A paused run resumes with the same identifiers and `resume: {"approvalId": "…"}`. The server requires a requester-owned approved or rejected decision, restores its saved framework interrupt and supplies the decision itself. Browser approval booleans, changed arguments, foreign identifiers, pending decisions and replay are refused. Rejection executes no write. Approved synchronous local writes commit their effect and execution event together; failure rolls back the effect and records failure.

The server reads run identifiers, the newest input message and optional owned skill reference for a new run, and the approval identifier for resume. Browser skill selection cannot replace the stored revisions on resume. It loads history, tools, context and state itself. The response is `text/event-stream`, with one AG-UI event per `data:` line. The body limit is 1 MiB. Browser context, tools, state, instructions and identity cannot replace server state. Root runs offer reviewed writes; the single-call query helper offers read tools only. Active time and tool calls retain their budgets across resume. Root runs propose one effectful call at a time; parallel approval proposals stop before any request or write is created. The native tool-call identifier and frozen arguments bind each execution to its approval.

The complete synthetic stream is `app/contract/examples/chat-answer.sse`.

The run continues on the agent worker when the connection closes. `GET /api/agent/runs/:run/events?after=N` re-attaches to the run's newest stream (one stream per POST, a start or a resume): it replays every event after the first N, then follows the run until `RUN_FINISHED` or `RUN_ERROR`. `POST /api/agent/runs/:run/cancel` stops a queued or running run and returns 204; its stream ends with `RUN_ERROR` code `run_cancelled`. A run whose worker died ends with `run_interrupted` and is not retried automatically.

`GET /api/conversations/:id/run` returns the latest requester-owned recorded run for an active owned conversation. Running or paused records take precedence over terminal history; within either group, `startedAt` and then run UUID descend. With no run, `status` is `none`, identifiers and timestamps are null, and `canResume` is false. A recorded `running` status does not establish worker liveness. Timestamps describe the stored run: `endedAt` can be present for a paused stream and does not make that run terminal.

The response contains status, run UUID, timestamps, optional approval UUID/status, `canResume` and `lastEvent`, the number of events in the run's newest stream (null before any). It projects no native snapshot, model settings, credentials, messages, tool arguments or document content. A paused approval appears only when one well-formed native interrupt links to the actual requester/run/conversation approval record. Its persisted interrupt identity includes the run UUID. Missing or malformed linkage leaves approval fields null and `canResume` false. Pending approvals require a human decision; only approved or rejected records are eligible for explicit resume. Executed and failed approvals cannot invite replay. The GET makes no changes and starts no worker. The decision and run POST routes recheck the current stored approval and native state when used.

### Recover a paused run

1. Read `GET /api/conversations/:id/run` for the saved conversation's status.
2. Read `GET /api/approvals/:id` to review the current proposed change. Submit an explicit decision with `POST /api/approvals/:id/decision` and `{"approve": true}` or `{"approve": false}`.
3. Refresh the run and approval. When the matching decision is approved or rejected and `canResume` is true, explicitly continue through the chat route using the saved run and conversation UUIDs and `resume: {"approvalId": "…"}`.

A decision alone executes no work. Rejection continues without the proposed change. Repeated decisions while paused return `409`; a decision after the run finishes returns `404`. Replaying a completed resume returns `409`.

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

The enforced limits are 25 MiB per file and 50 files per conversation. Conversation and draft titles contain 1 to 200 characters. Conversation titles must include a non-whitespace character; draft bodies contain at most 100,000 characters.

Uploads carry `X-Simulated`; their sole source classification is a validated query value, defaulting to unclassified. Bytes stay in PostgreSQL, and filenames never select a filesystem path. Upload records pass through uploading, processing and ready. Downloads return exact bytes as an attachment with nosniff.

## 9. Datacube

Datacube operations use the access, error, request, approval and stream conventions above. The [datacube page](datacube.md) defines their behavior. Unknown parameters, units, facets or stages return `invalid_input`; unreadable missions, values or handles return `not_found`.

Find limits: 1 to 12 conditions and at most 20 missions in `find_missions`; 2 to 5 missions in `compare_missions`; at most 10 results in `similar_missions`; The table 5.2 agreements allow at most 100 submitted candidates and 200 rows in a saved view. `at` is a date (`YYYY-MM-DD`) and `knownAt` a timestamp; both default to the time of the request and are returned. Source numbers are decimal strings of at most 100 characters without exponent, NaN or Infinity.

Similarity selects all five facets by default. An explicit selection has one to five distinct names; duplicate or unknown names are invalid. Scores range from -1 to 1. Missing or unreadable facet texts have null sides and no source identifiers; missing current-model vectors yield null scores. At least half the selected facets, rounded up, must count. Comparable results sort by descending mean score and then mission key; other results follow. The JSON examples are `app/contract/examples/similar-missions.json` and `app/contract/examples/similarity-explanation.json`.

## 10. Document summary

`GET /summarize` returns one static sign-in and summary shell with the development page disabled. The two summary APIs require the real cookie and an allowed Origin. They create no draft, filing, workspace file, mission or accepted value. Originals remain in request and browser File memory only.

Uploads accept PDF, docx and xlsx with their actual media types. The limit is 25 MB, with at most 100,000 converted characters for summary input. An absent simulation label is refused. Malformed or unsupported input is `400`; an oversized source is `413`; an unknown, unreadable or hash-mismatched selected version is `404`. Unsupported model sentences or filing claims return safe `500` without a partial accepted summary.

`SummarySource.version` hashes exact original bytes. `blockVersion` hashes the full normalized block quote. A selected citation keeps the provider handle; an upload has no provider handle or version. Every sentence cites complete supporting source context. Provider-path evidence supplies selected tags; upload filing evidence includes source headings and quotes. Missing or conflicting filing values are null.

Until the six-input confidence calibration is defined, scores remain zero, the level is low and status is `insufficient_evidence`. Cited extracts remain visible and no draft or filing is written. Model self-rating is ignored. Upload originals download from the owned File object; exact quoted blocks remain readable on the page. Tokens never enter browser storage.

## 11. Version differences and citation checks

`CompareVersions` names one readable item and two positive integer versions. The response contains at most 500 ordered added, removed, changed or moved blocks, with nullable source and target positions and a truncation flag. Equal versions return an empty list. Missing or unreadable versions return 404.

`CitationCheck` holds one canonical handle. It returns current, changed, newer_version or withdrawn and the latest provider version. The original is re-read outside database transactions. A row locator checks only its table row. Live source permissions apply before original bytes are read. Nonpositive positions return 400; a missing or mismatched quote returns changed. No source text is returned by these two operations.

Document metadata, chunks, exploration, comparisons and original preview/download also check current provider availability, grants and the selected immutable version before serving indexed results or bytes. Unindexed withdrawal or an inaccessible current location returns `not_found`. Explicit historical versions remain readable for an active, permitted source; missing provider metadata, versions or original snapshots return `not_found`. Provider I/O occurs outside database transactions. `ValueEvidence` retains withdrawn provenance with an unknown value, cleared bounds, an empty quote and a nonnumeric conversion message; current resource permissions still apply.

## 12. Table candidate values

`POST /api/cube/candidates` accepts `PrepareValues` and returns `Candidates`. The handle must name a complete readable table chunk; a non-table input is 400 and a mismatched handle is 404. Mission, silo and stage default to source tags. A named mission must be readable, including its selected silo. Unknown scope keys or invalid dates are 400. A missing date is a problem and is never guessed.

Each candidate contains its source row, header, parameter, named element or null mission total, original numeric token and unit, converted interval, row citation and at most one problem. Numeric tokens are at most 100 characters. Cells without valid numbers use an empty source-number string; their source text remains available through the row citation. Empty and TBD cells have kind unknown; n/a has kind not_applicable. Neither has numeric bounds. Ranges convert both decimal bounds. Candidate preparation writes no domain rows.

Problem names are unknown header, no unit, unknown unit, not a number, outside the allowed bounds, mission not resolved and no valid-from date. A candidate takes the first applicable problem in that order. The top-level problems list includes every applicable scope and cell problem. Element recognition applies account grants even when no silo is specified. Explicit element names remain names; no unrecognized row is treated as a mission total.

`submit_values` accepts one to 100 distinct candidates with explicit mission, silo and event date. It executes through the requester-approved native MCP call. Direct HTTP publication returns 409 approval_required. The source chunks must still be current and readable; every candidate must match freshly interpreted source cells and the approved arguments. Decimal bounds are recomputed from the original source number and registered conversion. One transaction appends every value with its approval identifier and execution event. Failure appends no values, rejection writes nothing and replay is refused. The response contains the approval identifier and new value identifiers.

## 13. Types

The built request and response forms are [`app/contract/types.ts`](https://github.com/mchdeore/CRAFT/blob/main/app/contract/types.ts). They are not copied here. AG-UI messages use the protocol package schema.

## 14. Routes

Each built route's path, body and response form is in [`app/contract/routes.ts`](https://github.com/mchdeore/CRAFT/blob/main/app/contract/routes.ts). Table 5.1 lists the same routes.

## 15. Examples

Every built JSON route has a synthetic response example. Binary and `204` responses have no JSON body. Chat uses `app/contract/examples/chat-answer.sse`.

| Route | Response type | File |
|---|---|---|
| `GET /api/approvals` | `Approval[]` | `app/contract/examples/approvals.json` |
| `GET /api/approvals/:id` | `ApprovalDetail` | `app/contract/examples/approval-detail.json` |
| `POST /api/approvals/:id/decision` | `Approval` | `app/contract/examples/approval.json` |
| `GET /api/notebook-grants` | `NotebookGrant[]` | `app/contract/examples/notebook-grants.json` |
| `GET /api/tools/capabilities` | `CapabilityCatalog` | `app/contract/examples/capabilities.json` |
| `GET /api/library/documents` | `DocumentCatalog` | `app/contract/examples/document-catalog.json` |
| `GET /api/tools` | `ToolInfo[]` | `app/contract/examples/tools.json` |
| `POST /api/cube/candidates` | `Candidates` | `app/contract/examples/candidates.json` |
| `POST /api/cube/values` | `ValueReceipt` | `app/contract/examples/value-receipt.json` |
| `POST /api/library/citations/check` | `CitationStatus` | `app/contract/examples/citation-status.json` |
| `GET /api/library/documents/:item/compare` | `VersionDiff` | `app/contract/examples/version-diff.json` |
| `GET /health/live` | `Health` | `app/contract/examples/health-live.json` |
| `GET /health/ready` | `Health` | `app/contract/examples/health-ready.json` |
| `GET /health/queue` | `QueueHealth` | `app/contract/examples/queue-health.json` |
| `GET /api/jobs` | `JobPage` | `app/contract/examples/job-page.json` |
| `GET /api/jobs/:jobId` | `Job` | `app/contract/examples/job.json` |
| `POST /api/jobs/:jobId/retry` | `Job` | `app/contract/examples/job.json` |
| `POST /api/jobs/:jobId/cancel` | `Job` | `app/contract/examples/job.json` |
| `POST /api/library/search` | `LibraryHits` | `app/contract/examples/library-search.json` |
| `GET /api/library/documents/:item` | `LibraryDocument` | `app/contract/examples/library-document.json` |
| `GET /api/library/documents/:item/chunks` | `ChunkPage` | `app/contract/examples/chunks.json` |
| `POST /api/library/explore` | `ChunkPage` | `app/contract/examples/explore.json` |
| `POST /api/auth/logout` | `SignedOut` | `app/contract/examples/signed-out.json` |
| `POST /api/session` | `Me` | `app/contract/examples/session.json` |
| `GET /api/me` | `Me` | `app/contract/examples/me.json` |
| `PATCH /api/me` | `Me` | `app/contract/examples/me.json` |
| `POST /api/me/workspace-tokens` | `IssuedWorkspaceToken` | `app/contract/examples/workspace-token.json` |
| `GET /api/me/workspace-tokens` | `WorkspaceToken[]` | `app/contract/examples/workspace-tokens.json` |
| `GET /api/workspace` | `Workspace` | `app/contract/examples/workspace.json` |
| `POST /api/workspace/start` | `Workspace` | `app/contract/examples/workspace.json` |
| `POST /api/workspace/stop` | `Workspace` | `app/contract/examples/workspace-stopped.json` |
| `POST /api/workspace/run` | `CodeResult` | `app/contract/examples/code-result.json` |
| `PUT /api/workspace/files` | `SavedFile` | `app/contract/examples/saved-file.json` |
| `POST /api/workspace/notebooks/edit` | `EditedNotebook` | `app/contract/examples/edited-notebook.json` |
| `POST /api/workspace/charts` | `SavedChart` | `app/contract/examples/saved-chart.json` |
| `POST /api/workspace/notebooks/run` | `CodeResult` | `app/contract/examples/code-result.json` |
| `POST /api/workspace/notebooks/edit-granted` | `EditedNotebook` | `app/contract/examples/edited-notebook.json` |
| `POST /api/workspace/notebooks/runs` | `NotebookRunQueued` | `app/contract/examples/notebook-run.json` |
| `GET /api/cube/missions` | `Mission[]` | `app/contract/examples/missions.json` |
| `GET /api/cube/parameters` | `Parameter[]` | `app/contract/examples/parameters.json` |
| `POST /api/cube/find` | `FoundMissions` | `app/contract/examples/found-missions.json` |
| `GET /api/cube/missions/:key` | `MissionState` | `app/contract/examples/mission-state.json` |
| `POST /api/cube/compare` | `Comparison` | `app/contract/examples/comparison.json` |
| `GET /api/cube/values/:id` | `ValueEvidence` | `app/contract/examples/value-evidence.json` |
| `POST /api/summarize` | `SummaryResult` | `app/contract/examples/summarize-selected.json` |
| `POST /api/summarize/upload` | `SummaryResult` | `app/contract/examples/summarize-uploaded.json` |
| `POST /api/cube/similar` | `SimilarPage` | `app/contract/examples/similar-missions.json` |
| `POST /api/cube/similar/explain` | `SimilarityExplanation` | `app/contract/examples/similarity-explanation.json` |
| `GET /api/conversations` | `ConversationSummary[]` | `app/contract/examples/conversations.json` |
| `POST /api/conversations` | `ConversationSummary` | `app/contract/examples/conversation-summary.json` |
| `GET /api/conversations/:id` | `Conversation` | `app/contract/examples/conversation.json` |
| `GET /api/conversations/:id/run` | `RunRecovery` | `app/contract/examples/run-recovery.json` |
| `PATCH /api/conversations/:id` | `ConversationSummary` | `app/contract/examples/conversation-summary.json` |
| `GET /api/conversations/:id/files` | `WorkspaceFile[]` | `app/contract/examples/workspace-files.json` |
| `POST /api/conversations/:id/files` | `WorkspaceFile` | `app/contract/examples/workspace-file.json` |
| `POST /api/library/documents/:item/revisions` | `Revision` | `app/contract/examples/revision.json` |
| `GET /api/library/documents/:item/revisions` | `Revision[]` | `app/contract/examples/revisions.json` |
| `GET /api/library/documents/:item/revisions/:revision` | `Revision` | `app/contract/examples/revision.json` |
| `PUT /api/notes` | `Note` | `app/contract/examples/note.json` |
| `GET /api/notes` | `NoteSummary[]` | `app/contract/examples/notes.json` |
| `GET /api/notes/:id` | `Note` | `app/contract/examples/note.json` |
| `PUT /api/skills/:id` | `Skill` | `app/contract/examples/skill.json` |
| `GET /api/skills` | `SkillCatalog` | `app/contract/examples/skills.json` |
| `GET /api/skills/:id` | `Skill` | `app/contract/examples/skill.json` |
| `GET /api/skills/:id/versions` | `Skill` | `app/contract/examples/skill.json` |
| `POST /api/agent/ask` | `AskAgentResult` | `app/contract/examples/ask-agent.json` |
| `POST /api/tools/chart` | `ChartResult` | `app/contract/examples/chart.json` |
| `POST /api/tools/calculation-check` | `CalculationResult` | `app/contract/examples/calculation-check.json` |

## 16. Stand-ins and contract tests

`app/contract/examples/` holds synthetic responses and the complete chat stream. JSON examples are stand-ins with the same response forms as the real endpoints. Local runs use the real backend and the frozen synthetic dataset.

1. `app/contract/*.ts` converts to JSON Schema with the held TypeScript generator.
2. Built route rows equal the app's method/path pairs in both directions. Tool rows equal the registry's tool names.
3. Python forms match their TypeScript fields and required fields in the component contract checks.
4. Every built JSON route names an example. Examples validate against compiled response schemas. File responses are checked by status and media type.
5. Recorded and scripted-model chat events validate against `@ag-ui/core`; `craft.answer` also validates against `AnswerMeta`.

The two tables are checked by `test_contract_routes_match_app` and `test_contract_tools_match_registry` in `app/tests/test_contract.py`. The examples are checked by `test_contract_json_examples`. Any added or removed endpoint or tool requires a matching row in the same change.
