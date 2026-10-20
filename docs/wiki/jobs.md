# Model jobs

`packages/provider_fleet/` is a standalone model-job prototype. Application chat does not dispatch through this queue. Its bounded streaming queue provides backpressure inside one request, not durable job scheduling.

## 1. Model jobs

The `craft_fleet` library stores job specifications, dispatch attempts, control state, events and artifacts in SQLite. Provider configuration names the endpoint, deployment and protocol. A credential reference names a private configuration file and field; the controller reads its value at dispatch.

A job specifies a topic, source excerpts, output limit, deadline and deliverable. Research results contain a summary and quoted findings. Each quote is checked against the supplied source. Coding proposals contain files at exact allowed relative paths. The queue stores valid coding proposals for review.

Global concurrency is at most two, with one active request per deployment. The queue records conservative token reservations and reported usage. Completion and retry require the current attempt token. A task revision creates a new job and preserves the original specification. A known quota refusal can receive bounded retries. A timeout or transport failure retains an uncertain outcome.

Jobs can reduce their retry limit, and a queued job retains its selected deployment when configuration changes. A changed deployment requires a new revision. Provider request and response identifiers accompany safe outcome events. Optional text-token price estimates preserve currency, rate source and date; missing rates or incomplete usage leave the estimate unknown.

The command interface supports `add`, `list`, `show`, `run`, `pause`, `resume`, `cancel`, `revise` and `recover`. Pause stops new claims. A stop event lets current work finish. Cancellation interrupts the named running request. Recovery records abandoned work as uncertain. Submitted results are separate from routine outcome and usage events.

## 2. Related topics

See [components](components.md), [runtime](runtime.md), [documents](documents.md) and [development](development.md).
