# Documents and evidence

## Identity

A document ID identifies the item across name and location changes. An immutable source version records its content hash and provider version. Extracted blocks identify text locations and the extraction recipe. Markdown references and text representations are derived from those blocks.

The application provider stores generated originals and immutable versions in its local provider folder. PostgreSQL stores the derived library, not original bytes.

Application document metadata, chunks, exploration, version comparisons and original downloads check current provider availability and permissions as well as indexed access. A source withdrawn or moved outside the caller's grants is unavailable even before reconciliation updates the index. Explicit historical versions remain readable while their source is active, permitted and still provides that immutable version. Missing source records, versions or original snapshots return the same response as an unreadable source.

For document discovery in Chat, request titles and short sentence previews in a numbered table. A reply such as "none of those" can refine the search; a row number, title or "that one" can select a document when the reference is clear. Search hits are bounded matches, not a complete document inventory or whole-document summaries.

A whole-document summary reads the selected version's outline and every indexed block in batches of at most 20. The stored index summary is a generated summary of at most 150 words. Missing blocks or run limits require a partial-coverage label; extracted text does not guarantee coverage of images or unsupported content in the original. The existing agent budget remains 20 tool calls and 120 seconds per run.

Document versions expose **Last indexed** through `versions[].indexedAt`. This records index creation for that version, not the provider's last update or synchronization. The demo uses local simulated originals and retains their simulation labels; it does not imply a live external tenant. Supported conversion stores every extracted block and an embedding for each block, but actual original, block and matching-model vector coverage must be verified for the selected snapshot.

Provider metadata and original bytes are read outside database transactions. Protected operation responses, including metadata, files, authentication and errors, use `Cache-Control: no-store`. Citation checks can report withdrawal without returning source text. Cube value evidence keeps withdrawn provenance, marks the value unknown with cleared bounds, and returns an empty quote and a nonnumeric conversion message. Current resource permissions still apply.

Permission checks constrain visible rows before counts and ranking. A partial provider scan does not establish deletion. [Retrieval](documents.md) owns the application search behavior; [data](datacube.md) owns the application tables.

## Values

Advanced Cube analysis is deferred for this demo. Existing source-backed values and their approval checks retain their current behavior.

The application publishes a bounded source-candidate batch through `cube.submit_values` and the existing native MCP approval transaction. It rechecks current source chunks, mission and silo access, candidate identity and exact Decimal conversion. Every append-only value records its approval identifier. A failed batch rolls back all values; rejection and replay produce no additional value. The [API contract](api-contract.md) describes the request and receipt.

See [engineering datacube](datacube.md), [components](components.md), [accounts and access](access.md) and [runtime](runtime.md).

## Retrieval

Document retrieval runs in PostgreSQL over the library ([Library: chunks, summaries and citations](datacube.md#library-chunks-summaries-and-citations)). The agent reaches it through the `search` tool (`library.search`). The same request accepts mission, silo, stage and chunk/document filters. It has no title-only, content-only or semantic-only mode.

| Step | Now |
|---|---|
| Corpus | Labelled, simulated; written to the provider by `craft seed`, indexed by `craft index`; one chunk per heading section, page, slide or table region |
| Permission filter | Mission grants first ([Access](datacube.md#access)). Metadata filters alone don't count as authorization |
| Keyword | `websearch_to_tsquery` on chunk and summary `tsv`, `ts_rank_cd`, top 50 |
| Vector | Query embedding (1536 dimensions), cosine, HNSW, top 50 |
| Fusion | Normalized 0.7 semantic + 0.3 lexical score; deduplicate authorized results; at most two chunks per document section; top 8 |
| Neighbours | Each chunk result carries the chunks before and after it; `explore` widens the window |
| Further retrieval | Each call performs one search. The agent can make further search or exploration calls within its run budget; there is no separate two-retry search limit |
| Citations | The model cites chunks per claim (structured output). Shown as document, heading path, item and version, locator, simulated label, classification, quote; the preview route opens the original at the locator |

Titles and heading paths enter chunk embeddings; title text is not in the keyword index. Ranking scores order hits and do not measure answer confidence. The current answer confidence result stays zero and low ([Confidence](agent.md#confidence)).

`ask_agent` combines the runtime's final answer, citations, confidence and run ID in one result ([Combined answer](agent.md#combined-answer)). Citation handles identify item, immutable version, block, locator and quote hash. Current source availability and permissions are rechecked before reading or previewing their evidence.
