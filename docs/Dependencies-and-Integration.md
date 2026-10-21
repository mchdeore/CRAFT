# CRAFT Dependencies and Integration

<p class="meta">Version 1.0 | 19 October 2026</p>

The application implements authenticated chat, a personal JupyterLab workspace, saved skills, approvals, document and cube tools, chart rendering, conversation files and PostgreSQL records. This reference owns dependency pins, setup and verification commands. Commands describe checks to run; a recorded result applies only to its tested source and environment.

Application programming interface (API), Hypertext Transfer Protocol (HTTP), JavaScript Object Notation (JSON), Model Context Protocol (MCP) and Agent-User Interaction (AG-UI) describe the application boundaries.

## 1. Dependencies

`pyproject.toml` declares dependencies; `uv.lock` records the complete resolved inventory. `uv sync --locked` refuses an outdated lock. Dependency changes use `uv add` and the affected compatibility checks.

| Dependency | Declaration or pin | Use |
|---|---|---|
| Python | `==3.12.*`; interpreter and images `3.12.13` | Application and workspace runtime |
| uv | `0.12.23` | Environment and lock tool |
| uv_build | `>=0.12,<0.13` | Application and local-package build backend |
| Database image | `pgvector/pgvector:0.8.7-pg18` | PostgreSQL 18 with vectors |
| FastAPI | `>=0.141,<0.142`; lock `0.141.1` | HTTP application and framework adapter |
| uvicorn | `0.54.0` | ASGI server |
| msgspec | `0.22.0` | Strict operation shapes |
| SQLAlchemy | `2.0.54` | Tables, queries and short transactions |
| psycopg[binary] | `3.3.6` | PostgreSQL driver |
| Alembic | `1.20.0` | Schema revisions and drift checks |
| pgvector, Python | `0.5.1` | Vector column support |
| agent-framework-core | `1.21.0` | Agent, session and native MCP tools |
| agent-framework-foundry | `1.14.1` | Configured Responses adapter |
| agent-framework-ag-ui | `1.5.1` | Server stream adapter |
| mcp | `1.30.0` | Registry server and native transports |
| anyio | `4.15.1` | Bounded stream and cancellation cleanup |
| argon2-cffi | `25.1.0` | Local account password hashing |
| python-docx | `1.2.0` | Word conversion |
| pypdf | `6.19.0` | PDF text and locators |
| python-pptx | `1.0.2` | Slides, tables and notes |
| openpyxl | `3.1.5` | Literal and cached spreadsheet values |
| vl-convert-python | `1.9.0.post1` | Backend chart rendering |
| openai | `3.29.0` | Responses and embedding transport |
| azure-ai-projects | `2.7.0` | Foundry project adapter |
| docker, Python SDK | `7.2.0` | Owned container lifecycle |
| fpdf2, development group | `2.8.9` | Generated demo PDFs and test fixtures |
| pytest | `9.1.1` | Component and contract checks |
| Ruff | `0.16.10` | Lint and format |
| Pyright | `1.1.414` | Python types |
| MkDocs, documents group | `1.6.1` | Navigation site |

The production lifecycle package is version `0.1.0`; its process-control backend is pinned to `4.3.0`. Its declarations live in `packages/process_manager/pyproject.toml`.

The root development group also installs the standalone runtime and Python workspace packages. The application image installs production dependencies and copies the lifecycle package; it omits those prototypes.

The Python workspace prototype builds separately from `packages/python_workspace/Dockerfile` and its own lock. Its pins are ipykernel `6.29.5`, kernel transport `8.6.3`, NumPy `2.3.4`, pandas `2.3.3`, statsmodels `0.14.5` and vl-convert-python `1.9.0.post1`. The optional Wolfram transport uses httpx `0.28.1`; its build backend is setuptools `80.9.0`.

The shared [user workspace](wiki/workspace.md) builds separately from `packages/user_workspace/Dockerfile`. Its package pins MCP `1.30.0` and httpx `0.28.1`; its Jupyter environment pins JupyterLab `4.6.4`, Jupyter Server `2.21.1`, ipykernel `6.29.5`, jupyter-collaboration `5.0.4` and jupyter-server-nbmodel `0.2.9`. Its analysis extra pins NumPy `2.5.4`, Pandas `3.0.6`, SciPy `1.18.1`, Matplotlib `3.11.2`, DuckDB `1.5.6` and OpenPyXL `3.1.5`. The private per-owner control service uses `jupyter-mcp-server==2.2.4` and MCP SDK 2 in a separate environment. Native kernel binding uses the application's production notebook transport pinned to `8.6.3`. The package README describes provisioning.

| Selected transitive or contract dependency | Lock or pin | Use |
|---|---|---|
| ag-ui-protocol | `0.1.22` | Python stream events |
| jsonschema | `4.26.0` | Compiled response and answer schemas |
| azure-identity | `1.26.0` | Foundry dependency |
| httpx2 | `2.13.1` | Model HTTP transport |
| @ag-ui/core | `0.0.59` | Executable contract event schemas |
| ts-json-schema-generator | `2.9.0` | TypeScript contract schemas |

`api/package.json` and its lock own the contract JavaScript dependency tree. The tracked React/TypeScript/Vite frontend lives in `web/`; its package manifest and lock own the frontend dependency tree. It imports shared forms from `api/types.ts`, including authentication, conversations, skills and files, and uses the existing authenticated backend.

| Frontend dependency | Declaration or pin | Use |
|---|---|---|
| Node.js | `>=24 <25`; frontend CI selects `24` | Frontend tools and tests |
| React and React DOM | `^19.2.8`; lock `19.3.0` | Browser interface |
| TypeScript | `~6.0.2`; lock `6.0.3` | Frontend type checks |
| Vite | `^8.3.0`; lock `8.3.4` | Development server and production bundle |
| AG-UI JavaScript transport | `^0.0.59`; lock `0.0.59` | Agent stream transport |
| @assistant-ui/react | `^0.15.28`; lock `0.15.28` | Chat interface |
| @assistant-ui/react-ag-ui | `^0.0.69`; lock `0.0.69` | Chat stream adapter |
| ExcelJS | `^4.4.0`; lock `4.4.0` | Spreadsheet preview |
| fflate | `^0.8.3`; lock `0.8.3` | ZIP preflight and repacking |
| saxes | `^6.0.0`; lock `6.0.0` | Streaming XML preflight |

Spreadsheet preview limits archive and XML intake, worksheet dimensions, cells and merges, and rejects workbooks that exceed those limits.

The backend also provides `/dev` and `/summarize` browser shells. The documentation workflow builds information and downloads; the frontend workflow checks the frontend separately.

## 2. Local setup

Use Git, uv, a running Docker engine, Compose and Buildx; frontend development also needs Node.js 24 with npm. Run commands from the repository root unless shown otherwise. Keep the checkout and environment outside folders that download file contents on demand.

Copy `.env.example` to an untracked `.env`. Set separate administrative, owner and application database passwords. A previously initialized database volume keeps its original passwords. Choose a free database host port and use a new disposable volume for isolated checks.

```bash
cp .env.example .env
uv sync --locked
docker compose up -d --wait db
uv run alembic upgrade head
uv run alembic check
uv run craft seed base
uv run craft index
uv run craft users reset demo --as admin
uv run uvicorn app.main:app --reload
```

Base seeding creates synthetic accounts, grants, two missions and five provider documents. It sets no account password and does not index. Password reset uses a private prompt. The generated demo command performs its own indexing and seeded-value loading:

```bash
uv run craft seed demo --seed 1
```

Seed and index writes use owner database access. These host commands need the development dependencies, including the generated-PDF library. The application reads the provider folder; it does not seed or migrate at startup.

Enable `CRAFT_DEV_PAGE=1` for the optional `/dev` page. It supports sign-in, search, chat, approval recovery, rename, archive and conversation file operations. `/summarize` is available independently.

For the tracked frontend, install both npm dependency trees, then start the Vite server with the backend already running:

```bash
npm ci --prefix api --ignore-scripts --no-audit --no-fund
npm ci --prefix web --no-audit --no-fund
cd web
CRAFT_BACKEND_URL=http://127.0.0.1:8000 npm run dev
```

The development proxy forwards `/api`, `/health` and `/mcp` to the selected loopback backend. `CRAFT_BACKEND_URL` must be a single HTTP or HTTPS loopback origin without credentials, a path, query or fragment. The backend must allow the frontend Origin. Live mode is the default and uses authenticated sessions.

For a demo, seed the backend before starting the frontend. `SEED_DEMO_PASSWORD` gives the seeded accounts `demo`, `restricted` and `admin` that password; it needs at least `CRAFT_PASSWORD_MIN_LENGTH` characters (12 by default) and is never stored in the repository:

```bash
SEED_DEMO_PASSWORD='choose-a-local-password' uv run craft seed demo
```

`scripts/demo go` runs the whole local demo on the configured models: it wipes the demo database and workspace, builds the workspace image, migrates and seeds the database, starts one demo workspace, the backend and the frontend, then opens `http://localhost:5200`. `scripts/demo stop` stops them. The seeded accounts sign in with password `1` unless `DEMO_PASSWORD` is set. Model settings come from the checkout's `.env`; generated database passwords live in the ignored `var/demo/`.

The production build ignores environment files and includes only curated public assets. Serve its `web/dist/` output with the authenticated backend from the same origin and its `/api/` routes; the development proxy does not configure production serving. Return to the repository root for the following backend commands.

Stop the host server before starting the application container on port 8000:

```bash
docker compose up --build --wait --wait-timeout 180
curl -fsS localhost:8000/health/ready
docker compose down
```

`docker compose down` preserves the database volume. Windows development uses the Windows Subsystem for Linux and line-feed files. Browser and container compatibility must be checked in the selected environment.

## 3. Services and state

Compose starts `db` and `app`. The optional `mcp` profile uses the same application image, database settings and read-only provider mount, and starts `craft mcp --as <account>` over standard input and output in the app's network namespace. HTTP MCP is already mounted inside the app at `/mcp`; neither transport is a code runner or durable queue.

Database initialization runs once on an empty volume. It creates owner/application logins, development/test databases and the vector extension. Migrations run as the owner; application requests use the application login and per-table grants. Applied revisions run from 0001 through 0013. Readiness checks database access and migration head; liveness checks the running process.

The production image runs as numeric user `10001` and omits uv, development packages and build tools. Compose forwards application database, model, provider, Origin and MCP settings. The app receives no administrative or owner database password.

The application agent uses PostgreSQL conversations and sessions with native HTTP MCP. Its `LocalHTTPTool` makes a single dispatch attempt per ordinary MCP tool invocation. A lost response leaves the outcome unknown and raises an error without automatic reconnect or replay. Approved local effects consume their matching approval in the write transaction. The separate runtime prototype's `SingleAttemptMCP` suppresses tool-call replay; the application does not import it.

The optional lifecycle attachment starts with no services. Controller code supplies owned process/container specifications; HTTP and MCP cannot create them. Owned Jupyter workspaces connect to chat through private MCP controls and share files with the user. Native Python reads use only the selected kernel's active chat binding. The runtime and Python workspace prototypes remain separate from application chat.

## 4. Provider, retrieval and generated data

`CRAFT_PROVIDER_ROOT` defaults to `var/provider` on the host. Compose mounts it read-only at `/provider`. It stores generated originals and immutable versions outside PostgreSQL. The database keeps derived chunks, summaries, vectors, differences and approved cube values.

Indexing converts sources and makes model/embedding requests before short writes. Summaries and vectors come from the configured models, and each row records the model that made it. `craft index --rebuild` rebuilds representations when that model changes. Source availability and mission/silo grants constrain search and evidence reads.

Search combines keyword and vector candidates with normalized 0.7 semantic and 0.3 lexical weights. Ranking scores order hits; answer confidence stays zero and low. Exact citation handles and source checks are described in the specification.

The generated demo set has 125 source items, twelve missions in four families and 48 planted questions. It includes changed versions, moves, copies, mixed units, bilingual headers, unknown cells and three withdrawn cited sources. Ground truth lives at `<provider root>/.provider/ground-truth.json`. An unchanged completed seed adds no version or value; a changed baseline preserves earlier generations.

The separate document package supports local-originals and external-originals modes, extraction, navigation and graph views. Its OpenText adapter and relationship store are not application library routes.

## 5. Component and contract checks

Use only `craft_test` or a named disposable database for checks. Keep its records and provider folder separate from demonstration data. Each collected suite needs its declared database, Docker or service prerequisites.

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
CRAFT_DATABASE_NAME=craft_test uv run alembic upgrade head
CRAFT_DATABASE_NAME=craft_test uv run alembic check
npm ci --prefix api --ignore-scripts --no-audit --no-fund
docker build --tag craft-python-workspace:local-pilot packages/python_workspace
uv run pytest
```

| Area | Targeted command | Assertions |
|---|---|---|
| Registry and logging | `uv run pytest -q app/infrastructure/operations/tests app/tests/test_architecture.py` | Checked boundaries, short transactions and metadata-only logs |
| Contract | `uv run pytest -q app/tests/test_contract.py` | Built routes/tools, Python/TypeScript shapes, examples and stream schemas |
| Sessions | `uv run pytest -q app/accounts/tests` | Cookie, Origin, expiry, revocation and account ownership |
| Sources and retrieval | `uv run pytest -q app/infrastructure/provider/tests app/library/tests` | Immutable versions, conversion, current access, ranking and citations |
| Cube | `uv run pytest -q app/cube/tests` | Units, dated values, comparisons, candidate evidence and approved publication |
| Generated data | `uv run pytest -q app/seed/tests/test_demo.py` | Reproducible bytes, ground truth, versions and withdrawals |
| Chat and recovery | `uv run pytest -q app/agent/tests` | Events, owned history, limits, cancellation and persisted approval resume |
| Native MCP | `uv run pytest -q app/infrastructure/mcp/tests` | Transport parity, run-token checks, tool links and effect refusal |
| Conversations/files | `uv run pytest -q app/conversations/tests` | Nonblank titles, list order, archive behavior and bounded exact files |
| Skills and notes | `uv run pytest -q app/skills/tests app/notes/tests` | Owner scope, immutable revisions, save conflicts and mutation replay |
| Tools and summary | `uv run pytest -q app/tools/tests app/summarize/tests` | Chart/expression bounds, source extracts and confidence behavior |

Tests install the scripted model from `app/tests/support/model.py` in place of the configured provider and nothing outside tests uses it. Deterministic checks use real application handlers and isolated PostgreSQL. Database setup failures and missing live prerequisites are recorded separately from assertion failures. A source review or synthetic test does not establish live model quality or browser acceptance.

After installing the API and frontend npm dependencies, run the frontend checks from `web/`:

```bash
npm test
npm run lint
npm run build
```

`npm test` selects every `tests/*.test.mjs` file with the TypeScript test loader and retains production artifact test output outside the frontend source tree. `npm run lint` runs `eslint .`. `npm run build` runs `tsc -b && vite build`, so it checks TypeScript before writing the production bundle to `dist/`. There is no separate frontend type-check script.

## 6. Continuous integration

`.github/workflows/ci.yml` runs on pull requests and pushes to main using Ubuntu `24.04` and a disposable database. It installs the lock, checks Ruff/format/Pyright, initializes and migrates the database, installs contract dependencies, builds the restricted workspace test image and runs pytest plus the contract and stream suites.

The full-stack gate builds Compose, checks readiness and request IDs, verifies the non-root app and its password-variable boundary, then removes the disposable project volume. A local repeat needs free application/database ports and its own Compose project; never reset an existing corpus to reproduce that check.

`.github/workflows/frontend.yml` runs on pull requests and main pushes using Ubuntu `24.04` and Node.js `24`. It installs API dependencies with `npm ci --prefix api --ignore-scripts --no-audit --no-fund` and frontend dependencies with `npm ci --prefix web --no-audit --no-fund`, then runs `npm test`, `npm run lint` and `npm run build` from `web/`. It grants only `contents: read`, disables persisted checkout credentials and does not deploy the application. These checks complement the backend, contract and package workflow.

## 7. Documentation build

Public Markdown lives under `docs/`. The build prepares document metadata, renders with Pandoc, prints PDFs with Chrome/Chromium, then stages tracked public sources and generated downloads into a strict MkDocs site.

Prerequisites are Git, Python 3, Pandoc `3.11`, Poppler and Chrome/Chromium. The documents group pins MkDocs in section 1. `CHROME` selects the browser executable. The macOS workflow uses Chrome Headless Shell `155.0.8059.79`; downloaded tools have recorded SHA-256 checks. macOS uses Charter and Menlo for publication PDFs; Linux verifies generation with fallback fonts.

```bash
docs/tools/build.sh
uv run pytest -q docs/tools/tests/test_public_site.py
```

The default output is `docs/build`: rendered topics, named CRAFT PDFs, theme/search assets and `markdown/` downloads of the exact public source files. Each topic has a Markdown download and GitHub edit link. Source dates come from Git; changed local sources use the current date. The site check validates local links, source equality and the public artifact allowlist.

Diagrams use committed Mermaid source and SVG pairs. Their separate renderer pins Mermaid CLI and Mermaid `11.12.0` and needs Node/npm plus the selected Chrome:

```bash
docs/tools/diagrams.sh
```

## 8. Public documentation

`.github/workflows/docs.yml` builds on pull requests, main pushes and manual dispatch. Pull requests verify on Linux; other events use macOS. Only a non-pull-request event in a public repository uploads an artifact, and deployment additionally requires main.

Pages contains documentation and downloads. It does not build or publish the authenticated application, local prototypes, original documents, runtime configuration or private records. New public topics must be tracked and included in navigation before a complete site build.
