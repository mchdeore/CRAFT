# Dependencies

The outside services and tools CRAFT relies on, and why. Exact versions are not repeated here: Python packages are in `pyproject.toml` and `uv.lock`, the contract's in `app/contract/package.json`, images in `compose.yaml`, `app/Dockerfile` and `workspace/Dockerfile`. Settings are in [`.env.example`](https://github.com/mchdeore/CRAFT/blob/main/.env.example).

| Service or tool | Why CRAFT uses it | Configured by |
|---|---|---|
| Microsoft Foundry project | Every model call: the main and check chat models and query embeddings for search. Model features refuse to run until the endpoint and key are set. | `CRAFT_MODEL_ENDPOINT`, `CRAFT_MODEL_KEY`, `CRAFT_MODEL_MAIN`, `CRAFT_MODEL_CHECK`, `CRAFT_MODEL_EMBEDDING` |
| PostgreSQL 18 with pgvector | All records: accounts, conversations, runs, approvals, the document library with its vectors, the datacube, and the background job queue. | `CRAFT_DATABASE_*` and the three database passwords |
| Procrastinate | The job queue on PostgreSQL that the three workers read ([background jobs](processes.md#background-jobs)). | `CRAFT_WORKER_QUEUES`, `CRAFT_WORKER_CONCURRENCY` |
| Docker with Compose and Buildx | Runs the database, app and three workers, and each owner's JupyterLab and restricted Python workspace containers. On macOS this is Colima. | `compose.yaml`, `DOCKER_CONTEXT` |
| JupyterLab | The personal workspace beside chat, in its own image. | `workspace/` |
| Wolfram Alpha (optional) | Checked calculations when an app ID is configured. | `CRAFT_WOLFRAM_APP_ID` |
| uv | Python environment, lock and builds. | `pyproject.toml`, `uv.lock` |
| Node.js 24 with npm | The contract's type check. | `app/contract/package.json` |
| GitHub Pages | Publishes this wiki from `.public/wiki/` when it changes on `main`. | `.github/workflows/wiki.yml` |

Protocols: HTTP with JSON for ordinary calls, AG-UI events over Server-Sent Events for the chat stream, and MCP for agent tool calls. See [components](components.md) for who talks to whom.
