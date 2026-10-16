# 10. Retrieval

Document retrieval runs in PostgreSQL over the library (section 8.4). The agent reaches it through the `search` tool (`library.search`).

| Step | Now |
|---|---|
| Corpus | Labelled, simulated; written to the provider by `craft seed`, indexed by `craft index`; one chunk per heading section, page, slide or table region |
| Permission filter | Mission grants first (section 8.15). Metadata filters alone don't count as authorization |
| Keyword | `websearch_to_tsquery` on chunk and summary `tsv`, `ts_rank_cd`, top 50 |
| Vector | Query embedding (1536 dimensions), cosine, HNSW, top 50 |
| Fusion | Normalized 0.7 semantic + 0.3 lexical score; deduplicate authorized results; at most two chunks per document section; top 8 |
| Neighbours | Each chunk result carries the chunks before and after it; `explore` widens the window |
| Retry | If no chunk is found by both methods or part of the question is uncovered, search again with new terms, at most twice |
| Citations | The model cites chunks per claim (structured output). Shown as document, heading path, item and version, locator, simulated label, classification, quote; the preview route opens the original at the locator |
| Evaluation | `craft eval` scores keyword-only and fused search against the questions in the demo ground truth (section 8.17) |
