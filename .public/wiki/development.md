# Development

How to set up, run and check CRAFT. External services are listed in [dependencies](dependencies.md); exact versions live in `pyproject.toml`, `uv.lock` and `app/contract/package.json`. CRAFT is the backend only: the product interface is the separate `craft-frontend` repository, which compiles against `app/contract/`.

## Layout

```text
app/                     backend: FastAPI server, craft CLI and worker (Python module `app`)
  Dockerfile             built from the repository root: docker build -f app/Dockerfile .
  contract/              HTTP contract as code: types.ts, routes.ts, examples/ (the product frontend compiles against it)
  processes/             owned process and container lifecycle (supervisord and Docker backends)
  infrastructure/        access, settings, database (initdb.sh, migrations/), operations, MCP, provider, logging
  agent/ accounts/ conversations/ library/ cube/ approvals/ notes/ skills/ tools/ summarize/ health/ dev/
  seed/                  base seed (base/: accounts, grants, provider Markdown) and the generated demo set
workspace/               the personal JupyterLab workspace image: Dockerfile, the `craft` package (src/), built-in skills
.public/wiki/            this wiki, published by GitHub Pages
.local/                  private and never committed: assistant rules and skills, plans, fake data, demo and provider state, worktrees
scripts/check            quick and full checks
scripts/demo             the local demo
compose.yaml  pyproject.toml  uv.lock  .env.example
```

Each folder under `app/` is one feature: `handlers.py` declares operations, `service.py` coordinates a use case and picks short transactions, `storage.py` receives a Session and never commits, and `tests/` sits beside the code. Migrations are numbered revisions in `app/infrastructure/database/migrations/versions`; Alembic reads its settings from `[tool.alembic]` in `pyproject.toml`. Settings are listed once, with comments, in [`.env.example`](https://github.com/mchdeore/CRAFT/blob/main/.env.example).

## Setup

Use Git, uv, a running Docker engine, Compose and Buildx; the contract type check also needs Node.js 24 with npm. Run commands from the repository root unless shown otherwise. Keep the checkout outside iCloud or other folders that download file contents on demand.

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

For a demo, `SEED_DEMO_PASSWORD` gives the seeded accounts `demo`, `restricted` and `admin` that password; it needs at least `CRAFT_PASSWORD_MIN_LENGTH` characters (12 by default) and is never stored in the repository:

```bash
SEED_DEMO_PASSWORD='choose-a-local-password' uv run craft seed demo
```

`scripts/demo go` runs the whole local demo on the configured models: it wipes the demo database and workspace, builds the workspace image, migrates and seeds the database, starts one demo workspace and the backend, then opens the backend's `/dev` page at `http://localhost:18766/dev/`. `scripts/demo stop` stops them. The seeded accounts sign in with password `1` unless `DEMO_PASSWORD` is set. Model settings come from the checkout's `.env`; generated database passwords live in the ignored `.local/runtime/demo/`.

Stop the host server before starting the application container on port 8000:

```bash
docker compose up --build --wait --wait-timeout 180
curl -fsS localhost:8000/health/ready
docker compose down
```

`docker compose down` preserves the database volume. Windows development uses the Windows Subsystem for Linux and line-feed files. Browser and container compatibility must be checked in the selected environment.

## Checks

There is no CI. Run the checks yourself:

```bash
scripts/check        # quick: Ruff, format, Pyright and the fast tests; contract types when app/contract/ changed
scripts/check full   # milestones: lock, migrations, every test except live ones, the workspace image, Compose and contract types
```

`scripts/check` starts a disposable PostgreSQL container, `craft-check-db`, on port 55481 with the passwords from `.env`. A plain `uv run pytest` runs the quick set: tests marked `docker`, `live` or `slow` are left out. `uv run pytest -m ''` runs everything. In a worktree, link the main checkout's `.env` first.

Use only `craft_test` or a named disposable database for checks, never a real corpus. To run one area:

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

Set the backend's `CRAFT_MCP_URL` to its own `/mcp` address when it does not run on port 8000.

## Services and state

Compose starts `db`, `queue` (Valkey) and `app`; the `worker` profile adds the background worker. The optional `mcp` profile uses the same application image, database settings and read-only provider mount, and starts `craft mcp --as <account>` over standard input and output in the app's network namespace. HTTP MCP is already mounted inside the app at `/mcp`; neither transport is a code runner or durable queue.

Database initialization runs once on an empty volume. It creates owner/application logins, development/test databases and the vector extension. Migrations run as the owner; application requests use the application login and per-table grants. Readiness checks database access and migration head; liveness checks the running process.

The production image runs as numeric user `10001` and omits uv, development packages and build tools. Compose forwards application database, model, provider, Origin and MCP settings. The app receives no administrative or owner database password.

The application agent uses PostgreSQL conversations and sessions with native HTTP MCP. Its `LocalHTTPTool` makes a single dispatch attempt per ordinary MCP tool invocation. A lost response leaves the outcome unknown and raises an error without automatic reconnect or replay. Approved local effects consume their matching approval in the write transaction.

The optional lifecycle attachment starts with no services. Controller code supplies owned process/container specifications; HTTP and MCP cannot create them. Owned Jupyter workspaces connect to chat through private MCP controls and share files with the user. Native Python reads use only the selected kernel's active chat binding.

## Provider and generated data

`CRAFT_PROVIDER_ROOT` defaults to `.local/runtime/provider` on the host. Compose mounts it read-only at `/provider`. It stores generated originals and immutable versions outside PostgreSQL. The database keeps derived chunks, summaries, vectors, differences and approved cube values.

Indexing converts sources and makes model/embedding requests before short writes. Summaries and vectors come from the configured models, and each row records the model that made it. `craft index --rebuild` rebuilds representations when that model changes. Source availability and mission/silo grants constrain search and evidence reads.

Search combines keyword and vector candidates with normalized 0.7 semantic and 0.3 lexical weights. Ranking scores order hits; answer confidence stays zero and low. Exact citation handles and source checks are described in the specification.

The generated demo set has 125 source items, twelve missions in four families and 48 planted questions. It includes changed versions, moves, copies, mixed units, bilingual headers, unknown cells and three withdrawn cited sources. Ground truth lives at `<provider root>/.provider/ground-truth.json`. An unchanged completed seed adds no version or value; a changed baseline preserves earlier generations.


## Backend conventions

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP) and JavaScript Object Notation (JSON). Tool calls use the Model Context Protocol (MCP). Scripts use the command-line interface (CLI).

Every call enters through an `@operation` handler. The decorator checks and decodes, records observation, logs and maps errors. Services select short database transactions and pass a Session explicitly to storage.

```python
@operation(SIGNED_IN, get="/library/documents/{item}", tool=True)
def get_document(caller: Caller, request: DocumentRef) -> LibraryDocument:
    """Return one document the caller may read. Use when the user names a document."""
    return service.read_readable(caller, request.item, request.version)
```

`@operation(access, *, get|post|put|patch|delete=path, tool=False, effects=NONE, prepare_approved=None)`

| Declaration | Required | Values and rule |
|---|---|---|
| `access` (first argument) | yes | A permission (`"accounts.manage"`), `SIGNED_IN`, `PUBLIC`. No default: a missing one fails at import |
| `caller: Caller` (first parameter) | yes | Every call has a user scope. A handler without it fails at import |
| input | if the call takes input | Second parameter, a msgspec `Shape`. Path + query + body fill its fields. A RawFile shape receives bounded original bytes and declared file headers after access checks |
| output | yes | Return type: a `Shape`, `list[Shape]` or `None` (`204`). A binary HTTP route returns a Response; it is never a tool. An async generator = an event stream (chat) |
| `get=` … `delete=` | no | The HTTP route under `/api`. At most one |
| `tool=True` | no | Also an MCP tool. Needs a route and a docstring (what, when, one example) |
| `effects=` | no, default `NONE` | `NONE`, `DATA`, `DOCUMENT`, `EXTERNAL`, `FINAL`. An agent call with an effect other than `NONE` pauses for approval |
| `prepare_approved=` | no, default `None` | Internal preparation callback for an approved synchronous effect; returns a typed server-owned snapshot before the write transaction |
| names | generated | Operation `<feature>.<function>` (routes, logs); tool = `<function>` |
| errors | raised | `InvalidInput`, `NotSignedIn`, `Forbidden`, `NotFound`, `Conflict`, `RateLimited`. Other failures return `internal_error` |

What the decorator does on every call:

| # | Task | HTTP | MCP | Python | On failure |
|---|---|---|---|---|---|
| 1 | Start the call: request ID, call ID, parent call ID into context | New request ID, `X-Request-Id` | New request ID | The current call's IDs | |
| 2 | Find the caller | Session cookie | Run token | The `caller` argument | 401 `not_signed_in` |
| 3 | Check access: `require(caller, access)` | Yes | Yes | Yes | 403 `forbidden` |
| 4 | Decode the input into its `Shape` | Path + query + body bytes | `arguments` object | A `Shape` (or a dict, converted) | 400 `invalid_input` + `fields` |
| 5 | Approval gate (`effects` ≠ `NONE`) | | Approved, unexecuted, matching request | | `approval_required` |
| 6 | Service selects short database work: `READ ONLY` for reads; end before external work | Yes | Yes | Retains trusted caller scope | |
| 7 | Run the handler | Yes | Yes | Yes | |
| 8 | Service commits or rolls back its short transaction | Yes | Yes | Service controls commit | |
| 9 | Encode the output: `Shape` to JSON bytes (`camelCase`) | Response body | Structured content + summary | Returns the `Shape` itself | |
| 10 | Log: one JSON line + one `call_log` row, in its own transaction, with the database work counted | Yes | Yes | Yes | |
| 11 | Map errors to `ApiError` | Status + body | Tool error | Raises | Details only in the server log |

- Python calls run the full pipeline: `get_document(caller, DocumentRef(item=…))` is checked and logged (`origin=python`). No bypass. Scripts and the CLI call handlers this way.
- Streams call services with short database transactions; none spans a model call or approval wait.
- `uv run craft operations` prints every operation: name, route, tool, access, effects.

`@observe(name)`: times an internal step (a model call, an embedding, a search, any outside HTTP call) as a `step` row under the current call. It is never exposed, never grants access and never sits on an `@operation`.

Use `@observe` for slow or external hops under an operation (provider I/O, file conversion, outbound HTTP, model calls). Do not observe every database query; statement counts already attach to the current call. The public decorator surface stays `{@operation, @observe}` until a third cross-cutting concern hurts in three places. Owner indexing (`craft index`) is outside operations today, so it does not emit step rows and keeps the raw provider and convert paths. Shared library helpers that also run outside operations emit observe steps only when a call is already open.

### On FastAPI

- Registration: `create_app()` collects every `@operation` and calls `app.add_api_route` for each route. Routes come only from the registry (mounts: `/mcp`, `/summarize`, and `/dev` while it exists); hand-written routes are prohibited. FastAPI body models and OpenAPI are off.
- Threads: sync handlers (database) run in FastAPI's thread pool; async handlers (chat) on the event loop. Context variables carry the request, run and call IDs into both.

### The handler rule

- Every call enters through a handler: routes, tools, scripts and the CLI.
- A feature uses another feature only by calling that feature's handler, never its `service.py` or `storage.py`. The other feature's access check and log row always run. Trusted context and resource permissions persist; services choose transaction boundaries.
- Within a feature, the handler calls `service.py` when there is logic beyond one query. The service calls `storage.py` with an explicit Session. Storage never commits independently.
- Ownership uses one helper per table kind, such as `read_owned` or `list_owned`. A missing or unreadable record returns `404`.

### Shapes: msgspec

```python
@dataclass_transform(frozen_default=True, kw_only_default=True, field_specifiers=(msgspec.field,))
class Shape(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True, rename="camel"):
    """Base for every operation input and output."""
```

- msgspec provides strict immutable shapes, rejects unknown fields and distinguishes strings from numbers. Its generated JSON Schema supplies tool and contract forms.
- Decode: JSON bytes or a dict to a checked `Shape`, or `invalid_input` naming the field. Encode: a `Shape` to JSON bytes (`camelCase` and `snake_case` via `rename="camel"`).
- `@dataclass_transform`: tells type checkers every subclass is frozen and keyword-only, as msgspec already does at runtime.
- Constraints: `Annotated[str, Meta(min_length=1, max_length=200)]`. Rule: every operation input and output is a `Shape`.

### Roles and access

Each operation declares a permission. Account roles are rows in `user_roles` ([Database](datacube.md#database)); the permission map is versioned code. Roles load on every request. Agents receive tool permissions rather than account roles. The operation check, agent effect gate and resource check apply on every path. A missing or unreadable owned record returns `404`. [Human approval](agent.md#human-approval) defines the decision rule.

### Logging

- Each operation call and `@observe` step writes:
  - one JSON line to stdout (stdlib `logging`);
  - one `call_log` row ([Database](datacube.md#database)), in its own short transaction, so failed calls are logged too.
- Fields: kind (`operation`/`step`), name, origin (`http`/`mcp`/`python`), outcome, error code, duration, database statements + time, request ID, call ID + parent, user ID, sign-in session ID, conversation ID, run ID, tool-call ID.
- Each log row links its account, sign-in session, conversation, run, tool call, operation and database work. One `run_id` query shows a whole chat turn.
- Database work: a SQLAlchemy event hook counts statements and time per call. Nobody writes log calls by hand.
- Never logged: passwords, tokens, bodies, prompts, conversation or document text, query strings, exception messages. Uvicorn's access log is off; Outbound HTTP and Alembic loggers stay at `WARNING` (they print URLs).
- Logs aren't audit: approvals keep their own records.
- Deferred: OpenTelemetry export (Agent Framework already emits traces) to Azure Monitor; retention.

Health probes preserve availability when log storage is unavailable. Liveness emits its operation event to stdout without database work. Readiness emits a database row only when the database is ready; an unavailable result emits stdout only and keeps the three-second deadline.

The current HTTP MCP mount reuses the local registry server. Private typed agent
handlers resolve run-token digests through short read-only transactions and issue or
revoke credentials through short writes. They are neither routes nor tools. Current
roles and active run/account state are checked before protocol requests and tool
execution. The tool-call identifier arrives in `craft/toolCallId` metadata; parent
context comes from server-owned state. `agent.tool` observes the native outbound
call without input/output logging, and links to the same run and model tool call as
the checked `origin=mcp` operation. Native SDK private transport fields do not become
public top-level AG-UI event fields.
