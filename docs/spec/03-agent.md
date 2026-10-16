# 3. Agent

Agent-User Interaction (AG-UI) carries stream events. The Model Context Protocol (MCP) carries tool calls over Hypertext Transfer Protocol (HTTP). The application programming interface (API) uses JavaScript Object Notation (JSON). Secure network paths use Transport Layer Security (TLS).

## 3.1 Now: one agent

- One agent per conversation: Microsoft Agent Framework (open source).
- Runs inside the CRAFT backend, on a local machine: tools, approvals and records are proven end to end before anything is deployed.
- Acts only through CRAFT tools, as the signed-in user. Every call is checked and logged.
- Effectful actions use the requester approval gate in section 3.4.
- Answers cite passages and carry a confidence score (example method: §4.2).

| Component | Dependency or state |
|---|---|
| Configured `Agent` with model, instructions and MCP tools | `agent-framework-core` |
| `FoundryChatClient`, Responses API with `store=false` | `agent-framework-foundry` |
| `MCPStreamableHTTPTool` calling `/mcp` | Agent framework wrapper with `mcp` transport |
| `AgentFrameworkAgent`, stream adapter | `agent-framework-ag-ui` |
| History and runtime state | `messages` and `agent_sessions` |
| Versioned instructions | `app/agent/instructions.md`; every run records its version |
| Tool-call middleware | Copies the model tool-call identifier into MCP metadata, linking the run, tool call and operation in the call log |

## 3.2 One request

The server reads the conversation and run identifiers, the newest user message or resume entry. It loads history, tools and state itself. Section 5 defines transaction and operation boundaries; section 1 defines workspace scope.

```text
POST /api/agent/runs RunAgentInput: threadId, runId, and the new user message or resume
 1  caller from the cookie; the conversation must be theirs, else 404
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
    disconnect: status cancelled; saved state kept
```

```python
# app/agent/runtime.py: the one function that changes if runs move to a worker
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

## 3.3 Tools over MCP

MCP (Model Context Protocol): the standard way an agent lists and calls tools. In CRAFT, connection 3 only.

- A tool = an operation with `tool=True`. Name, description (the docstring) and JSON Schema (msgspec) are generated.
- Tool name = the function name (`search_documents`): lowercase letters, digits, `_`, at most 64, unique across the registry (startup check). OpenAI-family models reject dots, so the operation name (`documents.search_documents`) stays for routes and logs only.
- Agent side: `MCPStreamableHTTPTool(url, header_provider=<run token>, allowed_tools=[…], approval_mode=…)`.
- Tool-call ID: a function middleware puts the model's tool-call ID in the MCP request's `_meta` (`craft/toolCallId`). The server writes it to `call_log.tool_call_id`.
- Server side: the low-level `mcp` `Server`, mounted at `/mcp` in the same FastAPI app. Stateless, JSON responses.
- Auth: `Authorization: Bearer <run token>`. The token is random, short-lived and bound to the signed-in account and the run. Only its digest is stored on `agent_runs`; it is revoked at run end or pause. The agent never holds the cookie or a password.
- Allowlist: `allowed_tools` per agent. A tool not on it doesn't exist for that agent.
- Hosting: Now inside the CRAFT app on the laptop (`http://localhost:8000/mcp`), called by the in-process agent. Deferred: the same app on Azure, optionally called by a Foundry-hosted agent over a private network path + TLS (run token handed over by the backend). Tool code unchanged.

## 3.4 Human approval

Now:
- The requester approves or rejects the effectful request. The agent cannot decide. Low-confidence work follows the evidence gate in section 4.2.
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
| 6 | Resume | The browser sends `resume`; the server answers from the database decision and restores the session |
| 7 | Server gate | Runs only with an approved, unexecuted request for the same run, tool and argument hash. Then `executed`/`failed`, once |

```python
@operation("drafts.save", post="/drafts", effects=DOCUMENT, tool=True)
def save_draft(caller: Caller, request: NewDraft) -> Draft:
    """Save a draft document. Use when the user asks to keep a written draft."""
    return storage.insert_draft(caller, request)   # reached by the agent only after approval
```

- Kinds that pause: create or change a document, change data, run code or call an outside service, final or high-risk actions.
- Reasoning and retrieval path: a tool that pauses takes one extra argument, `reason`, added by the MCP layer and never passed to the operation; it is the request's reasoning. The retrieval path is the run's tool calls before the request, in order (tool, arguments, time).
- Rejection: returns to the agent as a refused tool call; the user can reply with a correction.
- Durability: pauses survive restarts (nothing waits in memory), and every step is an append-only event (§7). The same agent state spans the approval round trip. Resume reads the decision from the database. After a reload, `GET /api/conversations/:id` returns saved messages and `GET /api/approvals?status=pending` lists paused requests.
- Section 12 holds designated approvers, refusal of self-approval, eligibility, escalation, timeouts and request editing.

## 3.5 Safety

| Risk | Control |
|---|---|
| Agent exceeds the user | Tools run as the user, through the same permission check as the app |
| Injection from the browser | The server reads only the newest user message, the conversation and run IDs and `resume`. History, tools and instructions load on the server |
| Injection from documents | Retrieved text is data; tools allowlisted; effectful actions need a person |
| Leak through search | Permission filter before ranking; the model sees only readable passages |
| Content in logs | Allowlisted log fields only |
| Runaway run | Starting limits: 20 tool calls; 4,000 output tokens per model call; 120 s; 1 run per conversation; 3 per user. Tuned with the evaluation set |

## 3.6 Where it runs

Now: runs stay on the laptop while tools, approvals and records are demonstrated end to end. Section 12 defines deployment extensions.

| | Now | Deferred |
|---|---|---|
| Agent run | Inside the `POST /api/agent/runs` request, in the CRAFT process | Worker + queue (runs must survive a disconnect or scale), or a Foundry hosted agent (hosting adapter generally available) |
| Tools, model, records | `/mcp` on localhost, Foundry or `fake`, local PostgreSQL | The same `/mcp` on Azure, Foundry, Azure PostgreSQL |

- Run boundary (Now): a run lives inside the chat request. Closing the stream cancels the run; saved state survives. `runtime.run()` is the one function a move changes: packaging + deployment + a network path; the tools don't change.

## 3.7 Chat stream (AG-UI)

AG-UI: an open protocol of typed events that streams an agent run to a web page.

- `POST /api/agent/runs` returns `text/event-stream`. Other operations return HTTP JSON responses.
- Packages:
  - server: the agent-framework stream adapter;
  - browser: the AG-UI event libraries.
- The server holds the conversation:
  - the browser sends AG-UI's `RunAgentInput`; the server reads only `threadId`, `runId`, the newest user message and `resume`;
  - browser-defined tools are off;
  - `threadId` = conversation ID, owner checked every request.
- Answer metadata: an `ACTIVITY_SNAPSHOT` event, `activityType` `craft.answer`, content `{messageId, citations, confidence}`, after each assistant message. Saved as an activity message, so the saved history (AG-UI messages) shows the same.
- Stream events adhere to the browser event contract. Dependency compatibility is checked with the adapter.

## 3.8 Deferred: more agents

| Stage | Shape | Trigger |
|---|---|---|
| Sub-agents | Main agent hands a bounded job (summarize, extract figures, check citations) to a helper: short context, lighter model | A job is long, parallel or noisy |
| Specialists | Coordinator; Research; Analysis; Drafting; Review: own instructions, model, allowlist | CRAFT runs end to end |

- Same infrastructure: tools, checks, approvals, logs; a new agent is configuration. Every hand-off is recorded (job, reason, limits, result); the main agent stays accountable; a helper never exceeds the user.
- Patterns: sequential for reports; a coordination pattern for open questions. Approvals pause any pattern. Before starting: specialist list, evaluation questions proving the team beats one agent, delegation thresholds.
