# Documents and evidence

## 1. Identity

A document ID identifies the item across name and location changes. An immutable source version records its content hash and provider version. Extracted blocks identify text locations and the extraction recipe. Markdown references and text representations are derived from those blocks.

The document library stores local originals in its local-originals mode. External-originals mode retains the intended derived data and source references. Its simulated tenant exercises listing, versions, content, extraction and reconciliation.

Application document metadata, chunks, exploration, version comparisons and original downloads check current provider availability and permissions as well as indexed access. A source withdrawn or moved outside the caller's grants is unavailable even before reconciliation updates the index. Explicit historical versions remain readable while their source is active, permitted and still provides that immutable version. Missing source records, versions or original snapshots return the same response as an unreadable source.

Provider metadata and original bytes are read outside database transactions. Protected operation responses, including metadata, files, authentication and errors, use `Cache-Control: no-store`. Citation checks can report withdrawal without returning source text. Cube value evidence keeps withdrawn provenance, marks the value unknown with cleared bounds, and returns an empty quote and a nonnumeric conversion message. Current resource permissions still apply.

The OpenText tenant adapter reads Content Metadata Service file/version records and Content Storage Service V3 streams. It maps logical chronicle IDs separately from specific version IDs. A reported text rendition supplies derived line blocks and an extraction reference. Missing text capability leaves reconciliation partial. Listing and version cursors stay within the configured service origin, and scan limits prevent incomplete pages from establishing deletion.

## 2. Representation and relationships

Representations record purpose, text, source location and generation or extraction origin. Embeddings refer to a representation and preserve model, dimensions, input type, recipe and hash. Structural links, source-supported evidence links and similarity candidates retain their distinct meanings.

Permission checks constrain visible rows before counts and ranking. A partial provider scan does not establish deletion. [Retrieval](../spec/10-retrieval.md) owns the application search behavior; [data](../spec/07-data.md) owns the application tables.

The local navigation interface accepts typed mission, provider, silo, stage, classification, simulation, text and tag filters. It selects the latest eligible source version before text or tag matching. A knowledge cutoff also selects eligible extraction and tag records. Counts and pages share a short read snapshot. Version timelines record acquisition times alongside explicitly current mission/stage/silo metadata.

Local document search and graph views select currently readable, active source versions before returning text, counts or nodes. The default selects the latest eligible version; an explicit all-version view retains accessible history. A knowledge cutoff selects the latest extraction known at that time, with a deterministic tie between extraction attempts. Current withdrawal and permission checks still apply to historical views.

New representations record their creation time. A cutoff excludes a later-generated summary even when its source version was already known. Existing stored representations whose timing cannot be established remain available in current views and are excluded from historical views. The local store preserves their records and source identities when adding the timestamp field.

Structural links belong to document versions. Evidence links depend on their source extraction; similarity candidates depend on both endpoint representations. Re-extraction retires those derived edges from current views while preserving eligible historical views. Graph nodes and edges share one read snapshot and the same source scope, and readable isolated nodes remain visible.

The field catalog describes supported filters. Optional field-vector matching preserves model, dimensions, recipe and catalog hash and compares cosine similarity. It suggests field names; direct queries use the same fixed SQL and caller permission rules without a model.

## 3. Values

The local cube fixture retains exact values, explicit value states, event time, knowledge time and source evidence. Candidate values are separate from published values. Approved publication is atomic and idempotent; rejected candidates write no factual value. Withdrawn latest evidence produces a withheld result without reviving an older value.

The application publishes a bounded source-candidate batch through `cube.submit_values` and the existing native MCP approval transaction. It rechecks current source chunks, mission and silo access, candidate identity and exact Decimal conversion. Every append-only value records its approval identifier. A failed batch rolls back all values; rejection and replay produce no additional value. The [API contract](../API-Contract.md) describes the request and receipt.

See [engineering datacube](../spec/08-engineering-datacube.md), [components](components.md), [accounts and access](../spec/09-accounts-and-access.md) and [runtime](runtime.md).
