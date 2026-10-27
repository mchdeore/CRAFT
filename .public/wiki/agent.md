# Agent

Agent-User Interaction (AG-UI) carries stream events. The Model Context Protocol (MCP) carries tool calls over Hypertext Transfer Protocol (HTTP). The application programming interface (API) uses JavaScript Object Notation (JSON). Secure network paths use Transport Layer Security (TLS).

## Now: one agent

The current runtime uses the real framework agent and stream adapter with
server-owned conversation history and session state. Native `MCPStreamableHTTPTool`
connections call the same registry server as local standard input and output.
The server validates a run-token digest and current account/run state before each
request and tool call; it never authenticates from a browser cookie. The transport
offers allowlisted read tools and approval-required writes. The single-call query
helper offers read tools only. A paused root run accepts an owned approval identifier
and restores the persisted framework session, interrupt and requester decision.

- One agent per conversation: Microsoft Agent Framework (open source).
- Runs inside the CRAFT backend with local PostgreSQL records.
- Acts only through CRAFT tools, as the signed-in user. Every call is checked and logged.
- Effectful actions use the requester approval gate in [Human approval](agent.md#human-approval).
- Answers cite passages and carry the confidence result in [Confidence](agent.md#confidence).

| Component | Dependency or state |
|---|---|
| Configured `Agent` with model, instructions and MCP tools | `agent-framework-core` |
| `FoundryChatClient`, Responses API with `store=false` | `agent-framework-foundry` |
| `MCPStreamableHTTPTool` calling `/mcp` | Agent framework wrapper with `mcp` transport |
| `AgentFrameworkAgent`, stream adapter | `agent-framework-ag-ui` |
| History and runtime state | `messages` and `agent_sessions` |
| Versioned instructions | `app/agent/instructions.md`; every run records its version |
| Tool-call middleware | Copies the model tool-call identifier into MCP metadata, linking the run, tool call and operation in the call log |
| Saved skills | Owned immutable definitions, loaded by exact ID and revision as instructions and registered tool references |

`GET /api/tools/capabilities` reports native skill loading as installed. The Skills API saves definitions with an expected revision and mutation identifier, pages through owned definitions and reads exact revisions. A definition grants neither source permission nor approval. Selected and restored references retain their exact revision across an approval pause.

## One request

The server reads the conversation and run identifiers, the newest user message or resume entry. It loads history, tools and state itself. [Backend conventions](development.md#backend-conventions) defines transaction and operation boundaries; [wiki home](README.md) defines workspace scope.

```text
POST /api/agent/runs RunAgentInput: threadId, runId, and the new user message or resume
 0  caller from the cookie; the conversation must be theirs, else 404; store the input
    and queue an agent_run job; the request now follows the run's stored events
    (on worker-agent:)
 1  rebuild the caller from the job (IDs only); the input comes from agent_run_inputs
 2  insert agent_runs (status running) and issue a short-lived run token
 3  load the agent state from agent_sessions, or create it
 4  build the input on the server: history from messages, tools from the allowlist,
    instructions from the file
 5  run the agent; stream its events
 6  each tool call goes to /mcp and through the same checks as an HTTP call, as the user
 7  finished:   save messages and state; status finished; revoke the token;
                send craft.answer and RUN_FINISHED
    paused:     save the approval request and state; status paused; revoke the token;
                RUN_FINISHED with an interrupt
    failed:     status failed; RUN_ERROR with a safe code
    cancelled:  POST /api/agent/runs/{run}/cancel; status cancelled; RUN_ERROR run_cancelled
    worker died: status failed; RUN_ERROR run_interrupted; never re-run by itself
 8  every event is written to agent_run_events; a closed page does not stop the run
```

```python
# app/agent/runtime.py: the turn itself; app/agent/jobs.py runs it on worker-agent
async def run(caller: Caller, request: RunAgentInput) -> AsyncIterator[AguiEvent]:
    conversation = conversations.own(caller, request.thread_id)         # 404 if not theirs
    run = runs.start(caller, conversation)                              # issues the run token
    session = sessions.load(conversation)
    try:
        async for event in agui_agent.run(build_input(conversation, request, run), session):
            yield event
    finally:
        runs.finish(run, session)                                       # saves and revokes, always
```

Run states: `running` ends as `finished`, `failed` or `cancelled`, or becomes `paused` and returns to `running` after a decision and a resume request.

## Tools over MCP

MCP (Model Context Protocol): the standard way an agent lists and calls tools. In CRAFT, connections 3 and 5.

- One adapter, `app/infrastructure/mcp/`, builds the MCP server from the operation registry. Every operation with `tool=True` is a tool; nothing else is. No model runs behind the adapter.
- Two transports share the adapter: HTTP at `/mcp` with a run token for the agent, and standard input and output through `craft mcp --as <username>` for a local MCP application. The command line names a local account; calls log `origin=mcp`.
- Over standard input and output there is no run, so a tool with an effect other than `NONE` returns `approval_required`.

- List results use an `items` object in MCP structured content and the original JSON list in text content.
- A tool = an operation with `tool=True`. Name, description (the docstring) and JSON Schema (msgspec) are generated.
- Tool name = the function name (`search`): lowercase letters, digits, `_`, at most 64, unique across the registry (startup check). OpenAI-family models reject dots, so the operation name (`library.search`) stays for routes and logs only.
- Agent side: `MCPStreamableHTTPTool(url, header_provider=<run token>, allowed_tools=[…], approval_mode=…)`.
- Tool-call ID: a function middleware puts the model's tool-call ID in the MCP request's `_meta` (`craft/toolCallId`). The server writes it to `call_log.tool_call_id`.
- Server side: the low-level `mcp` `Server`, mounted at `/mcp` in the same FastAPI app. Stateless, JSON responses.
- Auth: `Authorization: Bearer <run token>`. The token is random, short-lived and bound to the signed-in account and the run. Only its digest is stored on `agent_runs`; it is revoked at run end or pause. The agent never holds the cookie or a password.
- Allowlist: `allowed_tools` per agent. A tool not on it doesn't exist for that agent.
- Current local transport: stateless JSON HTTP at `/mcp`. The token is 32 random bytes; its SHA-256 digest and expiry are stored. Paused, finished, failed, cancelled, expired or disabled principals are refused. Completion, cancellation and an interrupted run revoke the token.
- Trusted log context: parent request/call and sign-in session identifiers come from server-owned run state. MCP metadata supplies the model tool-call identifier only; it grants no identity or parent authority. The private outbound tool observation and checked operation share that identifier.
- The loopback target defaults to `http://127.0.0.1:8000/mcp`, configurable through `CRAFT_MCP_URL`. Remote hosts, credentials, query strings and fragments are refused. DNS rebinding protection stays enabled.
- The application's `LocalHTTPTool` makes a single dispatch attempt per ordinary MCP tool invocation. A lost response leaves the outcome unknown and raises an error without automatic reconnect or replay. It retains native result metadata, parsing, error and cancellation behavior, input filtering, authentication and run/tool-call identity. Approved local effects consume their matching approval in the effect transaction.
- HTTP MCP runs inside the CRAFT app; the agent runtime on `worker-agent` calls it, which is why that worker shares the app's network in Compose. The optional Compose MCP service starts the same image and registry over standard input and output. MCP transports carry tool calls; they are separate from code execution and the job queue.

## Human approval

Now:
- The requester approves or rejects the effectful request. The agent cannot decide. Low-confidence work follows the evidence gate in [Confidence](agent.md#confidence).
- Two layers:
  - the runtime pauses (user experience);
  - the server gate refuses to execute (security).

| Step | Piece | What happens |
|---|---|---|
| 1 | `@operation(effects=…)` | `DATA`, `DOCUMENT`, `EXTERNAL` or `FINAL` marks the tool approval-required |
| 2 | Agent Framework | `approval_mode` (generated) holds the model's tool call |
| 3 | AG-UI adapter | `RUN_FINISHED`, `outcome.type="interrupt"`, `reason:"tool_call"`: the run is paused for a decision |
| 4 | CRAFT | `approval_requests` (operation, arguments, `arguments_sha256`, evidence, reasoning, retrieval path, `interrupt_id`) + a `requested` event. Session saved. Run `paused` |
| 5 | Requester decision | `POST /api/approvals/:id/decision`, HTTP only. Agent callers refused. Decided once (unique index) |
| 6 | Resume | The browser sends `resume: {"approvalId": "…"}` with the same run and conversation. The server reads the owned decision and restores the session and native interrupt |
| 7 | Server gate | Runs only with an approved, unexecuted request for the same run, tool and argument hash. Rechecks frozen source citations. The database effect and `executed` event commit together; failure rolls back the effect and records `failed` |

Rejection runs no write. Changed arguments, foreign approval identifiers, undecided
requests and replay are refused. Active time and tool-call counts remain bounded
across resume; human decision time does not consume the active-time budget. Notes
are `DATA` effects. Synchronous local `DATA` and `DOCUMENT` effects use the shared
transaction. External and asynchronous effects remain refused until their own
idempotent execution and recovery boundary is available.

Root runs propose one effectful call at a time. Parallel approval proposals stop
before any request or write is created. Atomic multi-item changes belong in one
supported batch operation. The frozen native tool-call identifier binds execution
to its specific approval, including when two stored requests have identical arguments.

- Declared `DATA`, `DOCUMENT`, `EXTERNAL` and `FINAL` effects require approval. Unsupported external and asynchronous execution is refused.
- Reasoning and retrieval path: a tool that pauses takes one extra argument, `reason`, added by the MCP layer and never passed to the operation; it is the request's reasoning. The retrieval path is the run's tool calls before the request, in order (tool, arguments, time).
- Rejection: returns to the agent as a refused tool call; the user can reply with a correction.
- Durability: pauses survive restarts (nothing waits in memory), and every step is an append-only event ([Database](datacube.md#database)). The same agent state spans the approval round trip. Resume reads the decision from the database. After a reload, `GET /api/conversations/:id` returns saved messages and `GET /api/approvals?status=pending` lists paused requests.
- [Deferred](roadmap.md#deferred) holds designated approvers, refusal of self-approval, eligibility, escalation, timeouts and request editing.

## Safety

| Risk | Control |
|---|---|
| Agent exceeds the user | Tools run as the user, through the same permission check as the app |
| Injection from the browser | The server reads only the newest user message, the conversation and run IDs and `resume`. History, tools and instructions load on the server |
| Injection from documents | Retrieved text is data; tools allowlisted; effectful actions need a person |
| Leak through search | Permission filter before ranking; the model sees only readable passages |
| Content in logs | Allowlisted log fields only |
| Runaway run | Starting limits: 20 tool calls; 4,000 output tokens per model call; 120 s; 1 run per conversation; 3 per user. Tuned with the evaluation set |

## Where it runs

Runs execute inside the CRAFT backend. The model adapter uses the configured provider, and records live in PostgreSQL.

- A run lives inside the chat request. Closing the stream cancels the run; saved state survives.

## Chat stream (AG-UI)

AG-UI: an open protocol of typed events that streams an agent run to a web page.

- `POST /api/agent/runs` returns `text/event-stream`, and so does `GET /api/agent/runs/{run}/events`. Other operations return HTTP JSON responses.
- Packages:
  - server: the agent-framework stream adapter;
  - browser: the AG-UI event libraries.
- The server holds the conversation:
  - the browser sends AG-UI's `RunAgentInput`; the server reads `threadId`, `runId`, the newest user message, `resume` and an optional exact skill reference;
  - browser-defined tools are off;
  - `threadId` = conversation ID, owner checked every request.
- Answer metadata: an `ACTIVITY_SNAPSHOT` event, `activityType` `craft.answer`, content `{messageId, citations, confidence}`, after each completed nonempty assistant text message. Empty tool-plan envelopes carry no answer metadata. Saved as an activity message, so the saved history (AG-UI messages) shows the same.
- Stream events adhere to the browser event contract. Dependency compatibility is checked with the adapter.

The input JSON body is at most 1 MiB. Unknown browser history, context, tools,
instructions and identity cannot replace server records. Only the newest user text
is appended. Run identifiers are globally unique; reuse and an active conversation
return conflict, while a fourth active run for an account returns rate limited.
Starts use a short account-serialized transaction. Model and tool waits occur after
it ends. Terminal events follow durable message and session saves.

The turn runs as an `agent_run` job on the `agent` queue ([background jobs](processes.md#background-jobs)), so a user can leave the page and come back to the answer or the approval request. The request stores the input in `agent_run_inputs` and streams events as the worker writes them to `agent_run_events`; one `LISTEN` connection per app process wakes the open streams. A closed connection no longer stops the run. Each POST (a start or a resume) is one numbered stream of events:

- `GET /api/agent/runs/{run}/events?after=N` re-attaches: it replays the run's newest stream after the first N events, then follows it until `RUN_FINISHED` or `RUN_ERROR`. `GET /api/conversations/{id}/run` returns `lastEvent`, the number of events so far.
- `POST /api/agent/runs/{run}/cancel` stops a queued or running turn; the stream ends with `RUN_ERROR` `run_cancelled`.
- A turn whose worker died ends `failed` with `RUN_ERROR` `run_interrupted`. It is never retried automatically: tools can change things and every attempt costs model money.
- A turn no worker takes within 60 seconds fails instead of waiting.

Events and inputs are deleted a day after the run ends; the conversation's messages stay the lasting record. Inside the worker, a bounded producer keeps native MCP connection, calls and close on their owning task.

Each model call uses the configured output limit, at most 4,000 tokens.
A run allows 20 tool calls and 120 seconds. Handled stream errors record failed
operation outcomes. Logs contain identifiers, timings and outcome codes only.
Citations carry current library handles. Confidence stays zero and low until the
six-input calibration exists; cited extracts remain visible and drafting is stopped.

Each completed text answer also saves a `craft.retrieval` activity. It names the answer message, run, selected model, instruction version and ordered tool/arguments/time path. The record is append-only conversation history. Later runs preserve it. Public and newly saved message envelopes exclude private transport fields.

## Combined answer

`POST /api/agent/ask`, also the `ask_agent` MCP tool, runs the same runtime with read tools only. It uses an owned supplied conversation or creates one, and returns the final answer text, citations, confidence and run ID as one JSON result. It cannot be called recursively from an active agent run.

## Models and confidence

The application programming interface (API) gives confidence as a score, level and reason. Search ranking orders readable evidence; it is separate from answer confidence.

### Models

| Role | Configured model | Effort | Setting |
|---|---|---|---|
| Main agent | GPT-5.2 | medium | `CRAFT_MODEL_MAIN` |
| Groundedness check | GPT-5.2 | low | `CRAFT_MODEL_CHECK` |
| Embeddings | text-embedding-3-large, 1536 dimensions | | `CRAFT_MODEL_EMBEDDING` |

- Every model, summary and embedding call goes to the configured provider. Without an endpoint and key the server still starts, serves sign-in, documents and the datacube, and refuses model calls.
- Responses API calls use `store=false`. Conversations live in CRAFT's PostgreSQL. Each call has an output-token limit.
- `CRAFT_MODEL_ENDPOINT` selects the Foundry project endpoint. `CRAFT_MODEL_KEY` supplies the local model API key. Configured mode requires an HTTPS endpoint and a key.
- `CRAFT_MODEL_MAX_OUTPUT_TOKENS` accepts 16 to 16,000 tokens and defaults to 4,000. Reasoning tokens count toward it, so agents that propose code need more than the default. Main and groundedness calls retain their selected model, effort, budget and storage setting.
- `CRAFT_AGENT_RUN_SECONDS` accepts 30 to 900 seconds and defaults to 120. It caps one agent run's active time; time spent waiting for an approval does not count, but active time before and after it does.
- Service-owned conversation references and software development kit (SDK) console payload logging are refused. Model requests run outside database transactions through private observations.
- Embeddings use the selected embedding model and 1,536 dimensions. The frozen library's vectors come from `text-embedding-3-large`; semantic queries use matching-model vectors only, so keep that model.
- Instructions live in `app/agent/instructions.md`; each run records their version.

### Confidence

The API form is `Confidence {score, level, reason}`. Current agent answers and document summaries return score zero and level low; filing confidence also stays zero. They retain readable cited findings and describe evidence as insufficient for drafting. Model self-ratings do not change the score.

| Evidence input | Meaning |
|---|---|
| Coverage | Evidence for every part of the question |
| Citation quality | Claim support and citation validity |
| Traceability | Block version, revision and source locator |
| Source agreement | Agreement and conflicts across independent sources |
| Retrieval quality | Relevant authorized passages and retrieval path |
| Unsupported claims | Assertions without supporting evidence |

The summary preserves complete source sentences or table rows, including their negation and context. Every cited block must support its extract. Filing suggestions use literal field labels and values. Citation identity establishes a source reference; it does not establish that every interpretation is supported.

### Ranking

PostgreSQL search combines normalized semantic and lexical scores with weights 0.7 and 0.3. Permission and current-source checks constrain candidates before results are returned. A hit score ranks evidence within the search; it is not a probability or a calibrated confidence score. [Retrieval](documents.md#retrieval) owns retrieval limits and citation handles.
