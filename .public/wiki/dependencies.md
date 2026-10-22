# Dependencies

The outside services and tools CRAFT relies on, and why. Exact versions are not repeated here: Python packages are in `pyproject.toml` and `uv.lock`, the frontend's in `web/package.json`, the contract's in `app/contract/package.json`, images in `compose.yaml`, `app/Dockerfile` and `workspace/Dockerfile`. Settings are in [`.env.example`](https://github.com/mchdeore/CRAFT/blob/main/.env.example).

| Service or tool | Why CRAFT uses it | Configured by |
|---|---|---|
| Microsoft Foundry project | Every model call: the main and check chat models, embeddings for search, and OCR for scanned PDFs and images. Model features refuse to run until the endpoint and key are set. | `CRAFT_MODEL_ENDPOINT`, `CRAFT_MODEL_KEY`, `CRAFT_MODEL_MAIN`, `CRAFT_MODEL_CHECK`, `CRAFT_MODEL_EMBEDDING`, `CRAFT_MODEL_OCR` |
| PostgreSQL 18 with pgvector | All records: accounts, conversations, runs, approvals, the document library with its vectors, and the datacube. | `CRAFT_DATABASE_*` and the three database passwords |
| Valkey (Redis protocol) | The durable job queue the background worker reads, used for indexing and OCR jobs. | `CRAFT_QUEUE_URL`, `CRAFT_QUEUE_NAME` |
| Docker with Compose and Buildx | Runs the database, queue, app and worker, and each owner's JupyterLab and restricted Python workspace containers. On macOS this is Colima. | `compose.yaml`, `DOCKER_CONTEXT` |
| JupyterLab | The personal workspace beside chat, in its own image. | `workspace/` |
| Wolfram Alpha (optional) | Checked calculations when an app ID is configured. | `CRAFT_WOLFRAM_APP_ID` |
| uv | Python environment, lock and builds. | `pyproject.toml`, `uv.lock` |
| Node.js 24 with npm | The frontend and the contract's schema checks. | `web/package.json`, `app/contract/package.json` |
| GitHub Pages | Publishes this wiki from `.public/wiki/` when it changes on `main`. | `.github/workflows/wiki.yml` |

Protocols: HTTP with JSON for ordinary calls, AG-UI events over Server-Sent Events for the chat stream, and MCP for agent tool calls. See [components](components.md) for who talks to whom.
