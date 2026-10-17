# Documents and evidence

## 1. Identity

A document ID identifies the item across name and location changes. An immutable source version records its content hash and provider version. Extracted blocks identify text locations and the extraction recipe. Markdown references and text representations are derived from those blocks.

The document library stores local originals in its local-originals mode. External-originals mode retains the intended derived data and source references. Its simulated tenant exercises listing, versions, content, extraction and reconciliation.

The OpenText tenant adapter reads Content Metadata Service file/version records and Content Storage Service V3 streams. It maps logical chronicle IDs separately from specific version IDs. A reported text rendition supplies derived line blocks and an extraction reference. Missing text capability leaves reconciliation partial. Listing and version cursors stay within the configured service origin, and scan limits prevent incomplete pages from establishing deletion.

## 2. Representation and relationships

Representations record purpose, text, source location and generation or extraction origin. Embeddings refer to a representation and preserve model, dimensions, input type, recipe and hash. Structural links, source-supported evidence links and similarity candidates retain their distinct meanings.

Permission checks constrain visible rows before counts and ranking. A partial provider scan does not establish deletion. [Retrieval](../spec/10-retrieval.md) owns the application search behavior; [data](../spec/07-data.md) owns the application tables.

The local navigation interface accepts typed mission, provider, silo, stage, classification, simulation, text and tag filters. It selects the latest eligible source version before text or tag matching. A knowledge cutoff also selects eligible extraction and tag records. Counts and pages share a short read snapshot. Version timelines record acquisition times alongside explicitly current mission/stage/silo metadata.

The field catalog describes supported filters. Optional field-vector matching preserves model, dimensions, recipe and catalog hash and compares cosine similarity. It suggests field names; direct queries use the same fixed SQL and caller permission rules without a model.

## 3. Values

The local cube fixture retains exact values, explicit value states, event time, knowledge time and source evidence. Candidate values are separate from published values. Approved publication is atomic and idempotent; rejected candidates write no factual value. Withdrawn latest evidence produces a withheld result without reviving an older value.

See [engineering datacube](../spec/08-engineering-datacube.md), [components](components.md), [accounts and access](../spec/09-accounts-and-access.md) and [runtime](runtime.md).
