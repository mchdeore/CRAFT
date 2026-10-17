# Local jobs and audio

## 1. Model jobs

The `craft_fleet` library stores job specifications, dispatch attempts, control state, events and artifacts in SQLite. Provider configuration names the endpoint, deployment and protocol. A credential reference names a private configuration file and field; the controller reads its value at dispatch.

A job specifies a topic, source excerpts, output limit, deadline and deliverable. Research results contain a summary and quoted findings. Each quote is checked against the supplied source. Coding proposals contain files at exact allowed relative paths. The queue stores valid coding proposals for review.

Global concurrency is at most two, with one active request per deployment. The queue records conservative token reservations and reported usage. Completion and retry require the current attempt token. A task revision creates a new job and preserves the original specification. A known quota refusal can receive bounded retries. A timeout or transport failure retains an uncertain outcome.

Jobs can reduce their retry limit, and a queued job retains its selected deployment when configuration changes. A changed deployment requires a new revision. Provider request and response identifiers accompany safe outcome events. Optional text-token price estimates preserve currency, rate source and date; missing rates or incomplete usage leave the estimate unknown.

The command interface supports `add`, `list`, `show`, `run`, `pause`, `resume`, `cancel`, `revise` and `recover`. Pause stops new claims. A stop event lets current work finish. Cancellation interrupts the named running request. Recovery records abandoned work as uncertain. Submitted results are separate from routine outcome and usage events.

## 2. Audio inbox

The `audio_inbox` library scans a local folder and records stable source fingerprints. It preserves immutable asset copies, transcript versions, segment references and suggested goals in its local store. A changed source becomes a new asset. Processing uses a claim token and retains uncertain work after interruption.

PCM audio is validated with Python's wave interface. Other supported formats use FFprobe metadata. Size, duration, timestamp and file bounds apply before processing. A transcriber receives the managed asset and returns text, model metadata and segments. Goal suggestions cite exact segment text. Review records approval, rejection or an edited goal, and Markdown export uses the stored transcript and review state.

The command interface supports `setup`, `scan`, `list`, `process`, `suggest`, `review`, `export` and `cancel`. Generated-data processing requires the explicit `--fake` option. The CLI refuses live processing when a service is not configured. Library callers supply transcriber and suggestion functions through the declared interfaces.

The `--providers` option selects a private JSON configuration with a deferred credential reference. Transcription and goal-model endpoints are configured separately. The transcription adapter uploads a verified bounded byte snapshot. Plain JSON text carries no generated timestamps; verbose responses retain reported segments. The goal adapter uses a strict Responses schema and the same quote/time validator as the inbox. Both adapters bound streamed responses and leave supplied HTTP clients under caller ownership.

Suggestion attempts retain process ownership and a claim token. Recovery marks abandoned attempts uncertain, while a live claim stays active. Completion cannot replace another claim. PCM WAV chunk preparation uses verified bytes, bounded temporary files and exact frame offsets. Transcript merging retains unknown timestamps and flags unresolved overlap; it removes only exact text/known-interval duplicates.

## 3. Research snapshots

The `research_data` library stores caller-fetched CSV and JSON snapshots with immutable hashes and acquisition records. Records include source URL, fetch/publication/observation times, schema, row and byte counts, license status and parent acquisitions for derived results. The catalog rejects a Git LFS pointer as dataset content and requires review when a dataset's shape changes.

The tennis baseline uses chronological availability, held-out dates and canonical player orientation. Financial research helpers use explicit payoff matrices, Decimal values, quote timestamps, fees, size limits and supplied fill assumptions. They retain source, market, rule and currency identity in the result. Partial-fill stress covers all full/unfilled subsets for up to eight legs. Expected value uses a caller-supplied probability distribution.

See [components](components.md), [runtime](runtime.md), [documents](documents.md) and [development](development.md).
