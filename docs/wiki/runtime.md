# Agent runtime

## Runs and tools

The application agent uses the registered operations and the [API contract](../API-Contract.md). The runtime library provides a local runner with a tool boundary, durable run state and memory decision support. A run retains its identity and state across a process restart.

Tools expose typed input and output. The controller supplies the allowed catalog and the decision boundary. Model output is validated before it changes run or stored state.

Signed-in users own their conversations and runs. `GET /api/tools/capabilities` returns the current registered tool catalog. The skill catalog returns an empty list with `not_installed` while no callable skill implementation is installed. `GET /api/library/documents` lists the user's readable indexed documents; source access and versions are rechecked before use.

The registry bridge derives tool bindings and approval requirements from declared operations. Root runs can propose writes; the single-call query helper sees read tools only. The native transport sends each tool call once. A lost response fails the call; connection recovery does not replay it. Server permission checks apply independently of the offered catalog.

## Approvals

A decision identifies the proposed action and its current context. Accepted or rejected decisions retain their result, and replay is checked against the stored action. Artifacts preserve output separately from routine status and usage logging.

The model job library maintains a local queue with pause, resume and cancellation. Provider routes refer to private credentials loaded at dispatch. Source quotes are checked against the supplied evidence; a quote match establishes a reference, while interpretation remains part of review.

The approval routes list and read requester-owned frozen actions and append an HTTP requester decision once. The database preserves request arguments, evidence and event history. Concurrent decisions serialize on the owned run. Approval decisions are absent from the model tool catalog.

A paused run resumes with its owned approval identifier. The server restores the saved framework interrupt and reads the decision itself. Approved synchronous local writes recheck frozen source citations and commit with their execution event. Failure rolls back the effect, rejection writes nothing and replay is refused. Tool calls and active time retain their budgets across resume. External writes require a separate recovery boundary.

Source preparation and citation checks run before the approved write transaction. An internal preparation callback can return an immutable snapshot bound to the specific caller, run, conversation, tool call, approval and argument digest. The guarded invocation rechecks the same unconsumed approval, current indexed source provenance and database grants before committing. Cube publication rederives candidates from the captured source blocks with the current dictionary and scope. Provider I/O is refused inside an ambient transaction; source state is sampled during preparation. Preparation failures are recorded only while that approval remains unconsumed.

Root runs review one effectful call at a time. Parallel proposals stop before any approval request or write is created. Execution binds the native tool-call identifier and frozen arguments to the specific approval. Native response streams close on completion, refusal and cancellation.

## Related topics

See [agent](../spec/03-agent.md), [operations](../spec/05-operations-and-logging.md), [models and confidence](../spec/04-models-and-confidence.md), [workspace](python.md), [documents](documents.md) and [components](components.md).
