# 4. Models and confidence

The application programming interface (API) gives confidence as a score, level and reason. Search ranking orders readable evidence; it is separate from answer confidence.

## 4.1 Models

| Role | Configured model | Effort | Setting |
|---|---|---|---|
| Main agent | GPT-5.2 | medium | `CRAFT_MODEL_MAIN` |
| Groundedness check | GPT-5.2 | low | `CRAFT_MODEL_CHECK` |
| Embeddings | text-embedding-3-large, 1536 dimensions | | `CRAFT_MODEL_EMBEDDING` |

- Every model, summary and embedding call goes to the configured provider. Without an endpoint and key the server still starts, serves sign-in, documents and the datacube, and refuses model calls.
- Responses API calls use `store=false`. Conversations live in CRAFT's PostgreSQL. Each call has an output-token limit.
- `CRAFT_MODEL_ENDPOINT` selects the Foundry project endpoint. `CRAFT_MODEL_KEY` supplies the local model API key. Configured mode requires an HTTPS endpoint and a key.
- `CRAFT_MODEL_MAX_OUTPUT_TOKENS` accepts 16 to 16,000 tokens and defaults to 4,000. Reasoning tokens count toward it, so agents that propose code need more than the default. Main and groundedness calls retain their selected model, effort, budget and storage setting.
- Service-owned conversation references and software development kit (SDK) console payload logging are refused. Model requests run outside database transactions through private observations.
- Embeddings use the selected embedding model and 1,536 dimensions. Indexing sends each document's texts in batches of up to 64. Changing the embedding model requires `craft index --rebuild`. Semantic queries use matching-model vectors only.
- Instructions live in `app/agent/instructions.md`; each run records their version.

## 4.2 Confidence

The API form is `Confidence {score, level, reason}`. Current agent answers and document summaries return score zero and level low; filing confidence also stays zero. They retain readable cited findings and describe evidence as insufficient for drafting. Model self-ratings do not change the score.

| Evidence input | Meaning |
|---|---|
| Coverage | Evidence for every part of the question |
| Citation quality | Claim support and citation validity |
| Traceability | Block version, revision and source locator |
| Source agreement | Agreement and conflicts across independent sources |
| Retrieval quality | Relevant authorized passages and retrieval path |
| Unsupported claims | Assertions without supporting evidence |

The summary preserves complete source sentences or table rows, including their negation and context. Every cited block must support its extract. Filing suggestions use literal field labels and values. Citation identity establishes a source reference; it does not establish that every interpretation is supported.

## 4.3 Ranking

PostgreSQL search combines normalized semantic and lexical scores with weights 0.7 and 0.3. Permission and current-source checks constrain candidates before results are returned. A hit score ranks evidence within the search; it is not a probability or a calibrated confidence score. Section 10 owns retrieval limits and citation handles.
