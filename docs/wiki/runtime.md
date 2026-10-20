# Agent runtime

## 1. Application runs and tools

The application agent uses the framework agent, registered operations and the [API contract](../API-Contract.md). Conversation history, session state and approval interrupts live in PostgreSQL. An active run belongs to its chat request; disconnect cancels it. A saved approval pause can be recovered after a restart.

Tools expose typed input and output. The controller supplies the allowed catalog and the decision boundary. Model output is validated before it changes run or stored state.

Signed-in accounts own their conversations and runs. `GET /api/tools` describes registered tools and their channels. `GET /api/tools/capabilities` returns read tools, up to 100 owned skill names and `skillsStatus: "installed"`. The paged Skills API supplies complete definitions and immutable revisions. A selected skill supplies instructions and actual tool references; it grants no source access or write approval. `GET /api/library/documents` lists currently readable indexed documents.

The registry derives tool bindings and approval requirements from declared operations. Root runs can propose writes; the `ask_agent` helper sees read tools only and returns a combined answer with citations and confidence. The application's `LocalHTTPTool` makes a single dispatch attempt per ordinary MCP tool invocation. A lost response leaves the outcome unknown and raises an error without automatic reconnect or replay. Approved local writes consume their matching approval in the effect transaction; a repeated invocation cannot consume it again.

MCP is the tool protocol. The app serves HTTP MCP at `/mcp`; `craft mcp --as <username>` serves the same registry over standard input and output. Compose starts services and has an optional MCP profile using the application image. The native registry does not execute arbitrary code or schedule durable jobs.

Configured owners also have [workspace](workspace.md) tools for shared Jupyter files and code execution. Native Python SDK reads borrow the active chat run's identity, call budget, deadline, retrieval trace and audit records. Access is confined to its selected notebook kernel and ends when the run finishes or pauses. The SDK refuses approval-gated operations before dispatch; chat keeps its approval and resume workflow.

## 2. Approvals

A decision identifies the proposed action and its current context. Accepted or rejected decisions retain their result, and replay is checked against the stored action. Artifacts preserve output separately from routine status and usage logging.

The approval routes list and read requester-owned frozen actions and append an HTTP requester decision once. The database preserves request arguments, evidence and event history. Concurrent decisions serialize on the owned run. Approval decisions are absent from the model tool catalog.

A paused run resumes with its owned approval identifier. The server restores the saved framework interrupt and reads the decision itself. Approved synchronous local writes recheck frozen source citations and commit with their execution event. Failure rolls back the effect, rejection writes nothing and replay is refused. Tool calls and active time retain their budgets across resume. External writes require a separate recovery boundary.

Source preparation and citation checks run before the approved write transaction. An internal preparation callback can return an immutable snapshot bound to the specific caller, run, conversation, tool call, approval and argument digest. The guarded invocation rechecks the same unconsumed approval, current indexed source provenance and database grants before committing. Cube publication rederives candidates from the captured source blocks with the current dictionary and scope. Provider I/O is refused inside an ambient transaction; source state is sampled during preparation. Preparation failures are recorded only while that approval remains unconsumed.

Root runs review one effectful call at a time. Parallel proposals stop before any approval request or write is created. Execution binds the native tool-call identifier and frozen arguments to the specific approval. Native response streams close on completion, refusal and cancellation.

## 3. Standalone runtime

`packages/runtime/` is a separate runner with local run-state and memory interfaces. Its `SingleAttemptMCP` transport sends a tool call once and fails on a lost response. The application does not use that class. The [Python workspace prototype](python.md) and [model job queue](jobs.md) are also standalone prototypes.

## 4. Related topics

See [agent](../spec/03-agent.md), [operations](../spec/05-operations-and-logging.md), [models and confidence](../spec/04-models-and-confidence.md), [workspace](workspace.md), [documents](documents.md) and [components](components.md).
