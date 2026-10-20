# 10. Retrieval

Document retrieval runs in PostgreSQL over the library (section 8.4). The agent reaches it through the `search` tool (`library.search`). The same request accepts mission, silo, stage and chunk/document filters. It has no title-only, content-only or semantic-only mode.

| Step | Now |
|---|---|
| Corpus | Labelled, simulated; written to the provider by `craft seed`, indexed by `craft index`; one chunk per heading section, page, slide or table region |
| Permission filter | Mission grants first (section 8.15). Metadata filters alone don't count as authorization |
| Keyword | `websearch_to_tsquery` on chunk and summary `tsv`, `ts_rank_cd`, top 50 |
| Vector | Query embedding (1536 dimensions), cosine, HNSW, top 50 |
| Fusion | Normalized 0.7 semantic + 0.3 lexical score; deduplicate authorized results; at most two chunks per document section; top 8 |
| Neighbours | Each chunk result carries the chunks before and after it; `explore` widens the window |
| Further retrieval | Each call performs one search. The agent can make further search or exploration calls within its run budget; there is no separate two-retry search limit |
| Citations | The model cites chunks per claim (structured output). Shown as document, heading path, item and version, locator, simulated label, classification, quote; the preview route opens the original at the locator |

Titles and heading paths enter chunk embeddings; title text is not in the keyword index. Ranking scores order hits and do not measure answer confidence. The current answer confidence result stays zero and low (section 4.2).

`ask_agent` combines the runtime's final answer, citations, confidence and run ID in one result (section 3.8). Citation handles identify item, immutable version, block, locator and quote hash. Current source availability and permissions are rechecked before reading or previewing their evidence.
