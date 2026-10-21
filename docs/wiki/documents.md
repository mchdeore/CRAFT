# Documents and evidence

## 1. Identity

A document ID identifies the item across name and location changes. An immutable source version records its content hash and provider version. Extracted blocks identify text locations and the extraction recipe. Markdown references and text representations are derived from those blocks.

The application provider stores generated originals and immutable versions in its local provider folder. PostgreSQL stores the derived library, not original bytes.

Application document metadata, chunks, exploration, version comparisons and original downloads check current provider availability and permissions as well as indexed access. A source withdrawn or moved outside the caller's grants is unavailable even before reconciliation updates the index. Explicit historical versions remain readable while their source is active, permitted and still provides that immutable version. Missing source records, versions or original snapshots return the same response as an unreadable source.

For document discovery in Chat, request titles and short sentence previews in a numbered table. A reply such as "none of those" can refine the search; a row number, title or "that one" can select a document when the reference is clear. Search hits are bounded matches, not a complete document inventory or whole-document summaries.

A whole-document summary reads the selected version's outline and every indexed block in batches of at most 20. The stored index summary is a generated summary of at most 150 words. Missing blocks or run limits require a partial-coverage label; extracted text does not guarantee coverage of images or unsupported content in the original. The existing agent budget remains 20 tool calls and 120 seconds per run.

Document versions expose **Last indexed** through `versions[].indexedAt`. This records index creation for that version, not the provider's last update or synchronization. The demo uses local simulated originals and retains their simulation labels; it does not imply a live external tenant. Supported conversion stores every extracted block and an embedding for each block, but actual original, block and matching-model vector coverage must be verified for the selected snapshot.

Provider metadata and original bytes are read outside database transactions. Protected operation responses, including metadata, files, authentication and errors, use `Cache-Control: no-store`. Citation checks can report withdrawal without returning source text. Cube value evidence keeps withdrawn provenance, marks the value unknown with cleared bounds, and returns an empty quote and a nonnumeric conversion message. Current resource permissions still apply.

Permission checks constrain visible rows before counts and ranking. A partial provider scan does not establish deletion. [Retrieval](../spec/10-retrieval.md) owns the application search behavior; [data](../spec/07-data.md) owns the application tables.

## 2. Values

Advanced Cube analysis is deferred for this demo. Existing source-backed values and their approval checks retain their current behavior.

The application publishes a bounded source-candidate batch through `cube.submit_values` and the existing native MCP approval transaction. It rechecks current source chunks, mission and silo access, candidate identity and exact Decimal conversion. Every append-only value records its approval identifier. A failed batch rolls back all values; rejection and replay produce no additional value. The [API contract](../API-Contract.md) describes the request and receipt.

See [engineering datacube](../spec/08-engineering-datacube.md), [components](components.md), [accounts and access](../spec/09-accounts-and-access.md) and [runtime](runtime.md).
