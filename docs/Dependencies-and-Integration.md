# CRAFT Dependencies and Integration

<p class="meta">Version 1.0 | 14 October 2026</p>

The application implements database setup, health routes, checked operations, the document provider and library, local accounts, server sessions and the model adapter. Continuous integration (CI) runs code, contract and document checks.

This document uses application programming interface (API), Hypertext Transfer Protocol (HTTP), JavaScript Object Notation (JSON), Asynchronous Server Gateway Interface (ASGI), Model Context Protocol (MCP), Agent-User Interaction (AG-UI), Transmission Control Protocol (TCP) Portable Document Format (PDF) and Hypertext Markup Language (HTML).

## 1. Dependencies

`pyproject.toml` declares direct dependencies and their allowed versions. `uv.lock` records the resolved versions. `uv sync --locked` creates the project-local `.venv` and refuses an outdated lock. Install dependencies with uv; direct `pip install` is not permitted. Dependency changes use `uv add` in a separate pull request with the affected compatibility checks. The lock also contains transitive dependencies; it remains the complete dependency inventory.

| Dependency | Current declaration or pin | Why this version is held |
|---|---|---|
| Python | `==3.12.*` | The project and lock require the same runtime series. |
| uv | `0.12.23` | CI uses one environment and lock tool version. |
| uv_build | `>=0.12,<0.13` | The build backend remains within the declared compatible series. |
| Database image | `pgvector/pgvector:0.8.7-pg18` | Compose and CI use the same PostgreSQL 18 image with the vector extension. |
| FastAPI | `>=0.141,<0.142`; lock `0.141.1` | The selected AG-UI adapter requires the older compatible FastAPI series. |
| uvicorn | `0.54.0` | The ASGI server remains fixed for local and container execution. |
| msgspec | `0.22.0` | Strict shapes and the agent dependencies share this compatible version (the agent framework requires msgspec `>=0.20,<0.23`). |
| SQLAlchemy | `2.0.54` | Tables, queries and transactions use the tested 2.0 interfaces. Results must be loaded before the session closes. |
| psycopg[binary] | `3.3.6` | The PostgreSQL driver and binary package resolve together. Connections use `postgresql+psycopg`. |
| Alembic | `1.20.0` | Schema changes and drift checks use one migration tool version. Generated revisions require review before upgrade. |
| pgvector, Python | `0.5.1` | SQLAlchemy vector column support is separate from the database extension version. |
| agent-framework-core | `1.21.0` | The compatibility probe checks the selected agent, session and MCP tool interfaces. An agent runtime remains planned. |
| agent-framework-foundry | `1.14.1` | The adapter uses `FoundryChatClient` for configured Responses calls. HTTP interception checks the endpoint, model roles, storage flag and budget. |
| agent-framework-ag-ui | `1.5.1` | The compatibility probe imports the FastAPI adapter. The application stream remains planned. |
| mcp | `1.30.0` | The agent framework requires MCP `>=1.24,<2`. A compatibility probe mounts a stateless JSON transport in FastAPI. The application's tool endpoint remains planned. |
| argon2-cffi | `25.1.0` | The compatibility probe checks Argon2id hashing with library defaults. Account flows remain planned. |
| python-docx | `1.2.0` | Reads Word paragraphs and tables in source order. |
| pypdf | `6.19.0` | Reads PDF text layers and page locators. Input-derived parser diagnostics are suppressed. |
| python-pptx | `1.0.2` | Reads slides, explicit tables and speaker notes. |
| openpyxl | `3.1.5` | Reads literal and cached spreadsheet values with links disabled. |
| fpdf2, test group | `2.8.9` | Builds synthetic text-layer and image-only PDF fixtures during tests. |
| openai | `3.29.0` | The installed framework uses this Responses and embedding transport. The version is unchanged. |
| azure-ai-projects | `2.7.0` | The project adapter supplies the documented local API key argument to the model transport. |
| pytest | `9.1.1` | Collection and execution use one test runner version. |
| Ruff | `0.16.10` | CI uses one lint and formatting rule set. |
| Pyright | `1.1.414` | CI checks Python 3.12 types with one checker version. |

The development interpreter selection and both Docker stages use Python `3.12.13`. The Dockerfile copies uv `0.12.23`. These are tracked in `.python-version` and `Dockerfile`; the Python series requirement remains in `pyproject.toml` and `uv.lock`.

| Selected transitive dependency | Current lock | Consequence |
|---|---|---|
| ag-ui-protocol | `0.1.22` | The Python adapter uses the pre-0.2 event library. Future browser event compatibility needs explicit verification. |
| jsonschema | `4.26.0` | It is installed through MCP. Its presence does not provide the planned TypeScript contract pipeline. |
| azure-identity | `1.26.0` | It is installed through the Foundry dependency. Managed identity integration remains deferred. |
| httpx2 | `2.13.1` | The installed model transport uses this HTTP implementation; checks intercept its actual requests. |

Browser integration selects `@ag-ui/core` and its browser adapter at `1.0.2`, and `ts-json-schema-generator` at `2.9.0`. These packages require a package manifest, lock, commands and CI wiring in the executable contract slice. Browser protocol 1.0 compatibility with the Python adapter must be checked there. Deployment authentication requires a fixed version with compatibility checks.

## 2. Local development

Prerequisites are Git, uv `0.12.23`, and a running Docker engine with the Compose and Buildx plugins. Run commands from the repository root. Keep the checkout and `.venv` outside folders that download file contents on demand. Use a new local database volume for the first setup. A previously initialized volume keeps its original database passwords.

On Windows, run under the Windows Subsystem for Linux (WSL) with line feed (LF) line endings; this platform has not been tested.

Copy the environment template once. Set `CRAFT_DB_ADMIN_PASSWORD`, `CRAFT_OWNER_DB_PASSWORD` and `CRAFT_APP_DB_PASSWORD` to separate local values before starting. Keep `.env` untracked. The template selects `localhost`, port `5432`, database `craft_dev` and log level `INFO`. If the host port is occupied, change `CRAFT_DATABASE_PORT` to a free port.

```bash
cp .env.example .env
uv sync --locked
docker compose up -d --wait db
uv run alembic upgrade head
uv run alembic check
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
uv run craft --version
```

Start the development server in a terminal. It stays running until interrupted.

```bash
uv run uvicorn app.main:app --reload
```

Check readiness from another terminal.

```bash
curl -fsS localhost:8000/health/ready
```

The successful response is `{"status":"ok"}`. Stop the development server before starting the full Compose application, since both use port 8000. The full stack builds and runs the deployment image locally.

```bash
docker compose up --build --wait --wait-timeout 180
curl -fsS localhost:8000/health/ready
docker compose down
```

`docker compose down` preserves the development database volume. S0's command-line interface supports `craft --version`. The planned commands are `craft seed <set>` (first `base`), `craft users add <username> [--admin]`, `craft users promote <username>`, `craft operations` and `craft call <operation> --as <username> <json>`. Account creation prompts for a password. Operation listing shows name, route, tool, access and effects. Operation calls use the named account and are logged. These local commands are implemented. Model settings select fake or configured calls. The temporary development page is off by default; enable it with `CRAFT_DEV_PAGE=1`.

## 3. Architecture rules and their checks

The following checks are defined in the S0 suite. The Scope column describes their assertions.

| Current rule or behavior | Collected test | Scope |
|---|---|---|
| Selected libraries can be used together | `test_dependencies_work_together` | Strict probe shapes, a separate MCP probe app, agent adapter imports, vector type construction and password hashing. It does not prove the application agent is implemented. |
| App construction makes no database request | `test_main_builds_the_app_without_a_database_call` | Imports the application entry point and checks its type. |
| Migration filenames follow the revision rule | `test_migration_names_follow_rule` | Names use `NNNN_<verb>_<thing>.py` and matching revision strings. The revision directory is empty in S0. |
| Migrations require the owner password | `test_migrations_refuse_to_run_without_the_owner_password` | Missing owner credentials stop migration setup. |
| A unit of work commits, rolls back and rejects writes when read-only | `test_unit_of_work_commits_and_rolls_back` | Uses real PostgreSQL and a synthetic probe table. Operation-level enforcement is future work. |
| Application credentials cannot create tables | `test_app_login_cannot_create_tables` | PostgreSQL denies a schema write from the application login. |
| Checks use a separate database | `test_test_database_is_separate` | Verifies the configured name and PostgreSQL's current database are `craft_test`. |
| Invalid settings name the failing field | `test_settings_fail_fast_names_field` | Covers an invalid port, missing application password, unknown field and invalid log level. |
| Settings do not expose secret values | `test_settings_never_print_secrets` | Checks representation and validation errors. |
| Administrative passwords are not application settings | `test_admin_and_owner_passwords_are_not_app_settings` | The parser excludes the two database setup credentials. |
| Responses carry a request identifier | `test_request_id_on_every_response` | Covers success, missing routes, wrong methods and an internal failure. |
| Request context reaches synchronous handlers | `test_request_id_reaches_thread_pool` | Checks propagation into FastAPI's thread pool. |
| HTTP logs are JSON and exclude content | `test_log_line_is_json_without_content` | Verifies allowed HTTP fields and omission of query, body and exception secrets. It does not check future operation log rows. |
| Errors use the intended status and code | `test_each_error_has_its_status_and_code` | Checks seven error classes and the request identifier. |
| Invalid input includes field errors | `test_invalid_input_lists_fields` | Checks the field error representation. |
| Liveness does not need the database | `test_health_live` | Returns HTTP 200 and an ok body with the database unavailable. |
| Readiness checks the database and migration head | `test_health_ready_checks_db_and_head` | Checks the ready response on the initialized test database. |
| Readiness fails with an unavailable database | `test_health_ready_503_when_db_down` | Returns HTTP 503 and an unavailable body. |
| Readiness has a bounded database wait | `test_health_ready_503_within_3_s_when_db_hangs` | The current assertion allows less than 3.5 seconds for the simulated hung connection. |
| Readiness fails behind the migration head | `test_health_ready_503_when_migrations_are_behind` | Supplies a synthetic expected revision missing from the target database. |
| The command-line version command works | `test_craft_command_prints_version` | Checks successful exit and the version prefix. |

Each later feature adds the following invariant checks with its implementation.

| Planned invariant | Planned verification |
|---|---|
| Identity comes only from the server cookie or run token | Reject body-supplied identity and compare HTTP, MCP and Python access behavior. |
| Every handler declares access and accepts trusted caller context | Reject incomplete declarations at import. Check resource permissions within the trusted context. |
| Routes come only from the operation registry | Compare all exposed routes with the registry and verify undeclared functions expose neither a route nor a tool. |
| Every tool also has a route and a model-safe unique name | Validate registry relationships and tool-name syntax at startup. |
| Read-only operations cannot write | Invoke a read-only operation that attempts a PostgreSQL write and require rejection. |
| Cross-feature calls enter through handlers | Check imports and access/logging behavior for nested calls. |
| The requester decides an effectful action; the agent cannot approve | Allow the requester to approve or reject and refuse agent decisions. Expanded approver policy is deferred. |
| Effectful agent calls need approved, unexecuted requests matching run, tool and argument hash | Refuse missing or mismatched approval and verify execution occurs once. |
| Append-only approval records never change | Verify update/delete are denied across access roles, including administrative access; operational traces remain separate. |
| Services select short transactions; storage receives a Session and never commits independently | Check transaction ownership, rollback and storage boundaries. Hold no transaction across a model call, search or approval wait. |
| Operations include observation; observe is private and grants no access | Check exposure and privilege boundaries and observe outside calls beneath the current operation. |
| Every call is logged and linked without content | Verify one operation row on success, one on failure, user/session/request fields, run/tool-call links, counted database work, observed outside calls and absence of payload. |
| All declared input and output shapes use msgspec | Enumerate registered shapes and reject incompatible types. The S0 probe checks only its own shape. |
| Back end and front end follow the executable API contract | Compare routes both ways, Python shapes with TypeScript types, responses and examples with generated JSON Schema, and stream events with the browser event library. |
| Schema changes use reviewed migrations | Upgrade an empty database, reject drift, preserve applied revisions and check append-only grants. The current filename guard alone does not enforce these rules. |
| The browser cannot supply history, tools or instructions | Attempt injection through the stream input and verify server-loaded state is used. |
| Search reveals only readable passages | Verify the permission filter runs before ranking and unreadable content never reaches results or model input. |

Future acceptance also checks all six confidence inputs (coverage, citation quality, traceability, source agreement, retrieval quality and unsupported claims), the normalized 0.7 semantic/0.3 lexical baseline, and conversation archive deletion of its workspace. Sign-out deletes nothing. These checks arrive with their features.

## 4. Test levels and databases

S0 collects 30 cases across eight files. The command below lists them without executing fixtures.

```bash
uv run pytest --collect-only -q -o addopts=
```

Unit checks cover settings, errors and entry points. API checks use FastAPI's `TestClient` for health and middleware behavior. Database checks use real PostgreSQL and synthetic probe data. The architecture check covers migration names, with an empty revision directory in S0. The dependency probe exercises installed libraries in a separate test application.

The autouse migration fixture points to `craft_test` and upgrades it to the current head before suite execution. There are no application migrations in S0. An owner connection creates and removes synthetic probe tables; an application connection exercises grants and transactions. `craft_dev` is for development, never the test corpus. Disposable CI databases may use trust authentication. Persistent development volumes use the passwords from `.env`.

Future component checks run handlers and storage against real `craft_test`, rather than a mocked database. Future contract checks validate generated schema, examples, responses and stream events. Future agent checks use a scripted fake model and deterministic embeddings with the real in-process MCP path. Real-model evaluation is deferred and runs after changes to models or instructions. The fake model needs no model key when that feature exists.

The scripted model uses the real agent, tools, middleware and stream. A search request invokes document search and cites a result; a draft request invokes the approval-gated save operation. Other scripted replies carry a citation. Fake embeddings derive from a hash. These behaviors arrive with the agent slice.

Fakes are reserved for outside services. Deterministic checks do not call external model services. Local PostgreSQL connections and a local synthetic hung socket are part of the present suite. No coverage percentage is specified. Each feature owns component and API checks under its `tests/` folder; fixtures use the real `craft_test` database. Each implemented rule needs a failing check for its breach; future invariants remain pending until their feature and verification exist.

## 5. CI workflow

`.github/workflows/ci.yml` runs on pull requests and pushes to `main`. The job uses Ubuntu `24.04`, a 20-minute limit and a disposable `pgvector/pgvector:0.8.7-pg18` service on port 5432. Checkout and uv setup actions are pinned to commit hashes. uv setup selects `0.12.23`. Owner and application passwords are generated for each job; database-service authentication is trust-based.

| Step | Command or action | Fails when |
|---|---|---|
| Checkout | Pinned checkout action with credentials persistence disabled | Source retrieval fails. |
| Environment | Pinned uv setup action, version `0.12.23` | The selected tool cannot be installed. |
| Disposable passwords | Generate owner and application passwords into the job environment | Random generation or environment-file writing fails. |
| Lock | `uv sync --locked` | The lock differs from the declaration or installation fails. |
| Lint and format | `uv run ruff check .` and `uv run ruff format --check .` | Code violates the configured checks or formatting. |
| Types | `uv run pyright` | A type error is reported. |
| Database initialization | `bash docker/initdb.sh`, with `PGHOST=localhost` | Role, database, extension or default-grant setup fails. This runs against the job's fresh service, not an initialized development database. |
| Migrations | `uv run alembic upgrade head` and `uv run alembic check` | Upgrade fails or schema drift is found. |
| Tests | `uv run pytest` | A collected check fails or fixture setup fails. |
| Contract guard | The shell block below | `api/` exists before its contract checks are wired. An absent directory reports a skip. |
| Full stack | The Compose block below | Build, startup, readiness, request header, user identity or password-variable assertions fail. |

The contract guard currently performs no schema validation. Its successful absent-directory path is runnable in S0.

```bash
if [ -d api ]; then
  echo "api/ exists: wire the contract tests into this step"
  exit 1
fi
echo "Skipped: api/ does not exist yet."
```

For local repetition after section 2's environment setup, run the lock, lint, format, type, migration and test commands there. Do not rerun `docker/initdb.sh` directly against an existing Compose database: Compose already invokes it on first boot. The workflow invokes it directly only because its CI database service has not loaded the project's initialization script.

The full-stack step reserves host port 5433 while CI's database service occupies 5432. These assertions can also run locally with the configured development port. The following complete block uses a separate disposable Compose project. It supplies temporary database passwords to both services and removes only that project's volume after the check.

```bash
(
  set -eu
  export COMPOSE_PROJECT_NAME=craft-ci-check
  export CRAFT_DATABASE_PORT=5433
  export CRAFT_DB_ADMIN_PASSWORD="$(openssl rand -hex 16)"
  export CRAFT_OWNER_DB_PASSWORD="$(openssl rand -hex 16)"
  export CRAFT_APP_DB_PASSWORD="$(openssl rand -hex 16)"
  trap 'docker compose down -v' EXIT
  docker compose up --build --wait --wait-timeout 180
  curl -fsi localhost:8000/health/ready | tee ready.txt
  grep -qi '^x-request-id:' ready.txt
  curl -fsS localhost:8000/health/ready | python3 -c 'import json,sys; assert json.load(sys.stdin) == {"status":"ok"}'
  test "$(docker compose exec -T app id -u)" = 10001
  test "$(docker compose exec -T app env | grep -c PASSWORD)" = 1
)
```

Stop any development server or application container occupying port 8000 before this block. Port 5433 must also be free. `ready.txt` contains a synthetic health response and may be removed after inspection. The shell trap stops the disposable project on success or failure. The tracked CI job currently performs cleanup at the end of its success path; the local wrapper adds cleanup for failed checks.

A completed passing CI run is required before merge. Repository enforcement depends on its configured rules and plan. Secret and dependency scans remain deferred. Deployment automation remains deferred.

## 6. Documents build and publishing

Public documents are Markdown under `docs/`. The build tools prepare metadata, render HTML with Pandoc, print PDFs with Chrome or Chromium and use Poppler to count pages and build the download index. The frozen print style uses Charter and Menlo on macOS. Linux builds check generation with the available fallback fonts; macOS builds provide the publication PDFs.

Build prerequisites are Git, Python 3, Pandoc `3.11`, Poppler and Chrome or Chromium. `CHROME` can select the browser executable. The documents workflow verifies downloaded tools with recorded SHA-256 values. On macOS it selects Chrome Headless Shell `155.0.8059.79`. Poppler and the Linux browser come from the runner environment rather than exact project version pins.

```bash
docs/tools/build.sh
```

Diagrams are Mermaid sources in `docs/spec/images/*.mmd`, each committed with the Scalable Vector Graphics (SVG) file rendered from it. `docs/tools/diagrams.sh` renders them with Mermaid CLI `11.12.0` and Mermaid `11.12.0` through `npm exec`, using `docs/tools/mermaid.json` (Charter, no HTML labels) and the Chrome that `CHROME` selects. It needs Node.js and npm. The documents build embeds each SVG in the PDF; it never runs Mermaid.

```bash
docs/tools/diagrams.sh
```

The output directory is `docs/build`. The build produces `CRAFT-Dependencies-and-Integration.pdf` alongside `CRAFT-Specification.pdf`, `CRAFT-API-Contract.pdf` and `index.html`. Dates come from each source's last Git change. Uncommitted sources use the current date and draft revision. The specification index receives page numbers in a second pass.

The documents workflow builds on every pull request, pushes to `main` and manual dispatch. Pull requests use Linux and verify only. Other events use macOS. The output check permits only the HTML index and named CRAFT PDFs. Private notes, source material and import packages stay outside publication.

Artifact upload requires a non-pull-request event in a public repository. Deployment additionally requires `main`. The private repository therefore builds documents without publishing them. Public document publishing requires repository settings and explicit approval. README document links are maintained by hand. A behavior change updates the specification in the same pull request.

## 7. Integration

Compose contains two services. `db` uses `pgvector/pgvector:0.8.7-pg18`. Its named volume mounts at `/var/lib/postgresql`, the PostgreSQL 18 image path. The host port defaults to 5432. Its health check uses TCP and becomes healthy after initialization, rather than during the image's first-boot setup.

`docker/initdb.sh` runs once on an empty volume as the database superuser. It creates `craft_owner` and `craft_app`, databases `craft_dev` and `craft_test`, and the vector extension in both. Default grants give the application login schema usage, table reads and sequence usage. Later migrations grant writes per table. Migrations use the owner login; the application uses its own login.

`app` builds from `Dockerfile`. The build stage installs locked runtime dependencies. The run stage omits uv, development dependencies and build tools, and runs as numeric user `10001`. Compose gives it only its application password and database/log settings. The database service receives the administrative, owner and application passwords. App liveness is the container health check. The separate readiness route checks database access and migration head.

The current settings are `CRAFT_APP_DB_PASSWORD`, `CRAFT_DATABASE_HOST`, `CRAFT_DATABASE_PORT`, `CRAFT_DATABASE_NAME` and `CRAFT_LOG_LEVEL`. `.env` supplies local values, with process environment values taking precedence. Validation rejects unknown application fields. The two setup password variables are explicitly excluded from application settings.

The model adapter uses the Responses API with `store=false`, an output-token limit on each call and CRAFT-owned conversation records. Settings select main, groundedness and embedding models through `CRAFT_MODEL_MAIN`, `CRAFT_MODEL_CHECK` and `CRAFT_MODEL_EMBEDDING`. `CRAFT_MODEL_ENDPOINT` selects the Foundry project endpoint, `CRAFT_MODEL_KEY` supplies a local model API key, and `CRAFT_MODEL_MAX_OUTPUT_TOKENS` sets the 16 to 4,000 token budget. Fake mode is the default and needs no endpoint or key. Configured calls have been intercepted locally; no live model endpoint has been called. Managed identity, deployment and subagent selection remain deferred.

The back end and front end are held to the API Contract. The executable `api/` TypeScript definitions, lock and examples cover the built routes. Their integration must compare routes in both directions, compare Python and TypeScript shapes, validate examples and responses, and validate AG-UI events despite the Python/browser version gap. FastAPI-generated OpenAPI is disabled in S0. HTTP JSON calls, the AG-UI server stream and the MCP tool path require their own contract checks when implemented.

Future simulated seed sets supply labelled demonstration data. Demonstrations use a seed set. A separate demonstration repository or branch is not required. The temporary development page at `/dev`, enabled by `CRAFT_DEV_PAGE=1`, supports sign-in and search (S2); chat (S3) and approval (S4) arrive with their features. It exercises the real browser cookie, Origin checks, stream and approval path while keeping contract adherence. It is removed in S5 when the front end is integrated. Seeded accounts are synthetic and require explicit private credential provisioning before browser sign-in.

Browser verification covers the Secure host cookie on localhost, particularly Safari. Use local Hypertext Transfer Protocol Secure (HTTPS) if the selected browser requires it.

Use a suitable Docker Desktop licence or a compatible container engine. Colima needs Compose and Buildx plugins and a shared checkout location. Podman and Rancher Desktop remain alternatives whose compatibility must be verified on the selected platform.

Deployment remains a consideration. The same application image and tools are intended for hosted operation, with environment-specific database and model configuration. Managed identity, data handling, transport protection, private networking, migrations, secret handling and database recovery require review before deployment. A worker and queue become relevant when runs must survive disconnects or scale. Registry, staging and production automation, telemetry export and retention remain deferred. Hosted verification runs against the deployment environment.

A deadline for each application transaction is added with the transaction-using feature. Readiness already has its own three-second deadline.

## 8. Datacube measures and component checks

Each build step adds its checks. A step that changes behavior updates section 8 of the specification in the same pull request.

| Step | Command | Expected result |
|---|---|---|
| B01 accounts and call log | `uv run pytest app/infrastructure/tests/test_migrations.py app/infrastructure/tests/test_grants.py` | Account constraints, explicit application grants and an isolated migration round trip pass |
| B03 library tables | `uv run pytest app/infrastructure/tests/test_library_schema.py` | Migration 0006 creates the library tables; `craft_app` reads them and writes only `mission_grants` |
| B05 MCP adapter | `uv run pytest app/infrastructure/mcp/tests/test_stdio.py` | Every tool operation is listed; calls run the operation pipeline and log `origin=mcp` |
| B06 provider and library | `uv run pytest app/infrastructure/provider/tests app/library/tests` | Scan, change feed, indexing, search, neighbours and access pass |
| B09 cube tables | `uv run pytest app/cube/tests/test_schema_access.py` | Migrations 0009 to 0011, append-only values and mission access pass |
| B10 office formats | `uv run pytest app/library/tests/test_convert.py` | Each format gives ordered chunks with locators; formulas and scripts never run |
| B11 demo seeder | `uv run pytest app/seed/tests/test_demo.py` | The same seed number gives the same bytes and ground truth |
| B12 cube reads | `uv run pytest app/cube/tests/test_parametric.py app/cube/tests/test_compare.py` | Units, conditions, state on a date, comparison and evidence pass |
| B15 version differences and citations | `uv run pytest app/library/tests/test_versions.py` | Edits, moves, deletes, block differences and citation status pass |
| B16 candidate values | `uv run pytest app/cube/tests/test_intake.py` | Header synonyms, unit conversion and every problem case pass |
| B17 facets and similarity | `uv run pytest app/library/tests/test_facets.py app/cube/tests/test_similarity.py` | Facet validator, facet vectors, ranking and not-comparable cases pass |
| B23 values through the gate | `uv run pytest app/cube/tests/test_approval_gate.py` | Values are written once, only after the requester approves |
| B24 saved views | `uv run pytest app/cube/tests/test_views.py` | A labelled, cited JSON view saves through approval |
| B25 evaluation | `uv run craft eval` | Fused recall at 8 is at least keyword-only recall on the demo ground truth |

Fixed synthetic queries record the median and 95th-percentile time and encoded bytes. Adversarial checks require zero permission leaks and zero unapproved writes.

## 9. Migration verification

Account and call-log migrations have immutable schema snapshots. Application grants are checked by table, including denied writes to the append-only call log. Migration round trips use a rollback-only connection on `craft_test`, preserving existing synthetic fixtures. Autogeneration compares the full table registry with the migrated database.

```bash
uv run pytest -q app/infrastructure/tests/test_migrations.py app/infrastructure/tests/test_grants.py
CRAFT_DATABASE_NAME=craft_test uv run alembic upgrade head
CRAFT_DATABASE_NAME=craft_test uv run alembic check
```

Conversation history and approval records use append-only application grants. Partial unique indexes enforce one approval decision and one execution outcome. The read-only status view selects the latest event. Run-token digests and runtime state remain separate from message content.

```bash
uv run pytest -q app/infrastructure/tests/test_append_only.py app/infrastructure/tests/test_migrations.py
```

## 10. Operation pipeline

The operation registry generates HTTP routes and validates names, access, input and output shapes. Sync handlers run in the thread pool; context follows nested calls and resets after errors. Services choose domain transactions. The log service appends only metadata in a separate short transaction. Observation steps remain private, and SQL counters read no statements or parameters. Health probe logging follows section 5's availability exceptions.

```bash
uv run pytest -q app/infrastructure/operations/tests app/tests/test_architecture.py
uv run pytest -q app/infrastructure/operations/tests/test_operation.py -k test_operation_curl_without_session
uv run pytest -q app/infrastructure/operations/tests/test_operation_logging.py -k "one_row or payload or observe"
```

## 11. Local MCP transport

The MCP adapter derives tool names, descriptions and input schemas from the operation registry. It performs no model call. Invalid arguments enter the same checked pipeline as HTTP and Python. Standard output carries protocol messages only; logs use standard error. Missing or disabled accounts exit with status 2. Effectful calls without a run return `approval_required`.

```bash
uv run craft mcp --as demo
uv run pytest -q app/infrastructure/mcp/tests/test_stdio.py
```

Start the command in the repository folder. An MCP application configuration uses:

```json
{"mcpServers":{"craft":{"command":"uv","args":["run","craft","mcp","--as","demo"],"cwd":"."}}}
```

MCP structured content requires an object. List results use an object with an `items` field; their text content remains the original JSON list.

## 12. Provider and library

The provider keeps original bytes and immutable versions outside the database. Indexing runs as the owner, converts text before short writes, and creates extract summaries and deterministic token-hash vectors in fake mode. Configured mode stores the selected embedding model on each vector. Mission grants apply before candidates, scores and counts. Reading and exploration remain within one readable version. Preview follows the file contract: sandboxed, no MIME sniffing, inline only for PDF, images and plain text. Other originals download.

Compose mounts the provider folder read-only at `/provider`. Host indexing uses `CRAFT_PROVIDER_ROOT` (default `var/provider`); the application only reads it. Seed and index orchestration use an explicit owner pool, separate from request credentials.

```bash
uv run craft seed base
uv run craft index
uv run craft operations
uv run craft call library.search --as demo '{"query":"radiator margin"}'
uv run pytest -q app/infrastructure/provider/tests app/library/tests app/tests/test_cli.py
```

Library read transactions use strict iterative HNSW scans so permission filters and dead index entries do not exhaust an initial candidate scan.

## 13. Account sessions and contract checks

Local sign-in verifies Argon2id hashes outside transactions and rechecks account state before creating a session. Tokens contain 32 random bytes; only digests enter PostgreSQL. Cookie flags, idle and absolute expiry, throttled keep-alive, revocation and current roles follow section 9. Authentication and permissions precede Origin rejection, preserving anonymous read denial. Credential and account changes serialize on the account row.

Account administration uses `craft users add`, `list`, `promote`, `reset` and `disable`; password entry uses a private prompt. An administrator can be named with `--as`. Session tests use synthetic accounts and a controlled clock.

The API package locks the adopted schema generator. Contract tests compile the actual TypeScript forms, validate examples and compare route keys with the running registry. CI installs the lock before testing.

```bash
npm ci --prefix api --ignore-scripts --no-audit --no-fund
uv run pytest -q app/accounts/tests/test_sessions.py app/accounts/tests/test_users_cli.py
uv run pytest -q app/tests/test_contract.py
```

## 14. Current account and browser demonstration

`GET /api/me` returns the enabled account and the roles resolved for the current request. Browser identity hints are rejected as unknown input; headers cannot replace the trusted identity. The optional `/dev` mount serves one static page and exposes no neighboring source files. Results use text nodes, including titles containing markup.

Use synthetic accounts and documents. Reset the demo credential through the local password prompt, or provision an environment-supplied test credential through the same account handler. Start the host server after stopping the application container; leave the database running.

```bash
uv run craft seed base
uv run craft index
uv run craft users reset demo --as admin
docker compose stop app
CRAFT_DEV_PAGE=1 uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a browser, open `http://localhost:8000/dev`, sign in, search for `radiator margin`, then sign out. In the second terminal, an unsigned request gives 401:

```bash
curl -s -o /tmp/craft-me.json -w '%{http_code}' http://localhost:8000/api/me
uv run pytest -q app/accounts/tests/test_me.py app/dev/tests/test_dev_page.py
uv run pytest -q app/accounts/tests/test_sessions.py app/tests/test_contract.py
```

A Chromium-based in-app browser on macOS exercised sign-in, search, literal markup-title rendering and sign-out. Real HTTP checks verified cookie flags, foreign-Origin rejection and revoked-session read denial. The exact embedded browser version, native browser cookie inspection, Safari and Windows/WSL have not been verified. Stop the host server with Ctrl-C before restarting `docker compose up --build --wait`.

## 15. Cube schema and mission access

Revisions 0009 through 0011 create mission reference rows, the parameter dictionary, append-only values and derived facet texts. Value constraints require both numbers for exact/range and neither for unknown/not applicable. Kind and facet are required. The application reads reference and facet tables and inserts values; stage maintenance uses the owner pool. Mission grants filter lists before name matching, ordering and limits.

```bash
uv run alembic upgrade head
uv run alembic check
uv run craft seed base
uv run craft missions stage msn-sim-01 st3-design
uv run craft call cube.list_missions --as demo '{}'
uv run pytest -q app/cube/tests/test_schema_access.py
```

## 16. Office conversion

The reader versions were released at least two weeks before 16 October 2026 and support Python 3.12. Tests generate made-up files inside their temporary folders; no binary fixture is tracked. Office package roots and the PDF signature must match the extension. Word paragraphs and tables retain source order. Slides keep speaker notes; tables in every format repeat their header for each window of at most 30 data rows. PDF table extraction uses aligned text columns. Scanned documents return `no text layer`.

Spreadsheet reads use `data_only=True` and `keep_links=False`. Formulas, macros and remote links never execute. Multiline cells render on one line; literal pipe and backslash characters are escaped so a cell cannot become an extra column. Conversion failures expose fixed reasons.

```bash
uv run python -c "import docx, pypdf, pptx, openpyxl; print('ok')"
uv run pytest -q app/library/tests/test_convert.py
```

## 17. Synthetic demo set

`craft seed demo --seed 1` writes a reproducible source set, indexes it, loads mission totals with row citations, then withdraws three cited source items and refreshes the index. A fresh set has 125 items, twelve missions in four families, 48 planted questions, twelve changed-version pairs, two moves and three copies with matching cells but different bytes. Spreadsheet headers alternate English and French; source units mix kg/g and W/kW. A TBD cell remains unknown, and unitless cells are not loaded as values. Component rows and three mission elements support later intake.

The ground-truth file is `<provider root>/.provider/ground-truth.json`. It records mission families, questions with stable handles, version and move records, withdrawals, copies, dated values and a source fingerprint. Fixed package properties, sorted ZIP entries and PDF creation dates keep bytes reproducible. An unchanged completed seed adds no source version or value. A changed baseline creates a new generation while earlier versions and values remain.

```bash
uv run craft seed demo --seed 1
uv run pytest -q app/seed/tests/test_demo.py
```

The tests use an owned disposable Compose database, compare two complete folders, verify source hashes and dated state, and run the optional local content check over every generated version. The optional audit path is supplied through `CONTENT_CHECK_SCRIPT`; it is configured for local validation and unavailable in CI. Runtime PDF writing uses the pinned PDF library; no test-only writer is needed by the seed command.

## 18. Model adapter

The adapter returns a fake or configured main and groundedness model compatible with the framework. The configured path uses the held Foundry and project packages, a local model API key and the documented project `get_openai_client(api_key=...)` interface. Project identity requests are unavailable in this slice. Responses storage, selected models, medium or low effort and output budgets are sealed before each request.

Configured embeddings enter a typed Python-only handler and an observed network step. Standalone indexing gets an operation parent; search preserves its trusted caller. Source conversion and embedding finish before short writes. Changing models needs `craft index --rebuild`; semantic search filters vector model names. Fake library indexing keeps its existing token-hash recipe. The separate fake model embedding reports its actual fake recipe.

SDK payload log namespaces are suppressed, and configured mode rejects the SDK console payload flag. Errors expose codes and metadata. Intercepted HTTP checks verify endpoint paths, selected role models, `store=false`, output budgets and 1,536-dimensional embeddings. Rebuild checks use an owned disposable database. No live model compatibility or quality result is implied.

```bash
uv run pytest -q app/agent/tests/test_model_adapter.py
uv run pytest -q app/agent/tests/test_model_adapter.py -k embedding
uv run pytest -q app/infrastructure/operations/tests/test_operation_logging.py
CRAFT_MODEL_MAIN=fake uv run craft index --rebuild
```
