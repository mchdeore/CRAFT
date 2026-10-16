# 4. Models and confidence

The application programming interface (API) gives confidence as a score, level and reason. Artificial intelligence (AI) search supplies evidence through the retrieval interface.

## 4.1 Models

| Role | Model | Effort | Setting |
|---|---|---|---|
| Main agent | GPT-5.2 | medium | `CRAFT_MODEL_MAIN` |
| Groundedness check | GPT-5.2 | low | `CRAFT_MODEL_CHECK` |
| Embeddings | text-embedding-3-large, 1536 dimensions | | `CRAFT_MODEL_EMBEDDING` |
| Sub-agents (Deferred) | GPT-5 mini | low | `CRAFT_MODEL_SUBAGENT` |

- Changing a model means changing its setting and passing the evaluation set. It requires no application-code change.
- `CRAFT_MODEL_MAIN=fake`: a scripted fake model + deterministic fake embeddings. No Foundry key. Used for deterministic verification and demos.
- Responses API calls use `store=false` to disable response storage. Conversations live in CRAFT's PostgreSQL. Every call has a `max_output_tokens` limit. Section 12 defines the data-handling gate.
- Instructions: `app/agent/instructions.md`, versioned in git. Each run records the version used.
- Access:
  - Now: a local development key from `.env`, simulated data only.
  - Deferred: managed identity + data handling meeting the president's demands (before real data).

## 4.2 Confidence

Confidence uses all six evidence inputs rather than the model's self-rating. The API form is `Confidence {score, level, reason}`. Calibration is recorded in section 12.

| Input | Required evidence |
|---|---|
| Coverage | Evidence for every part of the question |
| Citation quality | Claim support and citation validity |
| Traceability | Block version, revision and source locator |
| Source agreement | Agreement and conflicts across independent sources |
| Retrieval quality | Relevant authorized passages and retrieval path |
| Unsupported claims | Unsupported assertions reduce confidence |

- The output score lies between 0 and 1, with a level and evidence-based reason. The level follows calibrated thresholds. A reason can name agreement across sources or a cited table.
- A low-confidence result states that evidence is insufficient, shows the readable findings and stops drafting. Consequential work requires a recorded decision.
- Section 12 defines the calibration gate for weights and thresholds against the evaluation set. A fixed set of questions with known answers and sources measures expected citations, supported claims and confidence levels. Changes to a model, instructions or search method require this evaluation.

## 4.3 Hosted search and confidence

Azure deployment or a real corpus triggers hosted search and groundedness adapters behind the same retrieval interface, agent tools and API.

| Part | Now | Deferred hosted design |
|---|---|---|
| Index | PostgreSQL keyword index and vectors | Azure AI Search (Foundry IQ), rebuilt from the same sections |
| Ranking | Normalized 0.7 semantic and 0.3 lexical fusion | Hybrid search and semantic ranker; its score between 0 and 4 informs retrieval quality among the six inputs |
| Permissions | Required role on each document | Source-system permissions through connectors |
| Groundedness | Selected groundedness model, low effort | Azure groundedness detection |

Recalibrate thresholds when the index or confidence signals change.
