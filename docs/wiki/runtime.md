# Agent runtime

## 1. Boundary

The application agent uses the registered operations and the [API contract](../API-Contract.md). The runtime library provides a local runner with a tool boundary, durable run state and memory decision support. A run retains its identity and state across a process restart.

Tools expose typed input and output. The controller supplies the allowed catalog and the decision boundary. Model output is validated before it changes run or stored state.

## 2. Decisions and artifacts

A decision identifies the proposed action and its current context. Accepted or rejected decisions retain their result, and replay is checked against the stored action. Artifacts preserve output separately from routine status and usage logging.

The model job library maintains a local queue with pause, resume and cancellation. Provider routes refer to private credentials loaded at dispatch. Source quotes are checked against the supplied evidence; a quote match establishes a reference, while interpretation remains part of review.

## 3. Related topics

See [agent](../spec/03-agent.md), [operations](../spec/05-operations-and-logging.md), [models and confidence](../spec/04-models-and-confidence.md), [workspace](python.md), [documents](documents.md) and [components](components.md).
