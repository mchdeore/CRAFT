# 10. Retrieval

Document retrieval runs in PostgreSQL. `retrieval.search(query)` returns `Passage[]`. The agent reaches it through `documents.search_documents`.

| Step | Now |
|---|---|
| Corpus | Labelled, simulated; loaded by `craft seed`; split per section |
| Permission filter | `required_role` first. Metadata filters alone don't count as authorization |
| Keyword | `websearch_to_tsquery` on `tsv`, `ts_rank_cd`, top 50 |
| Vector | Query embedding (1536 dimensions), cosine, HNSW, top 50 |
| Fusion | Normalized 0.7 semantic + 0.3 lexical score; deduplicate authorized results, top 8 |
| Retry | If no passage is found by both methods or part of the question is uncovered, search again with new terms, at most twice |
| Citations | The model cites passage IDs per claim (structured output). Shown as document, section, version (the file's SHA-256), locator, simulated label, classification, quote; the preview route opens the original at the locator |
