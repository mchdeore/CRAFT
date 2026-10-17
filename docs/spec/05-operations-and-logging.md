# 5. Operations and logging

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

## 5.1 On FastAPI

- Registration: `create_app()` collects every `@operation` and calls `app.add_api_route` for each route. Routes come only from the registry (mounts: `/mcp`, `/summarize`, and `/dev` while it exists); hand-written routes are prohibited. FastAPI body models and OpenAPI are off.
- Threads: sync handlers (database) run in FastAPI's thread pool; async handlers (chat) on the event loop. Context variables carry the request, run and call IDs into both.

## 5.2 The handler rule

- Every call enters through a handler: routes, tools, scripts and the CLI.
- A feature uses another feature only by calling that feature's handler, never its `service.py` or `storage.py`. The other feature's access check and log row always run. Trusted context and resource permissions persist; services choose transaction boundaries.
- Within a feature, the handler calls `service.py` when there is logic beyond one query. The service calls `storage.py` with an explicit Session. Storage never commits independently.
- Ownership uses one helper per table kind, such as `read_owned` or `list_owned`. A missing or unreadable record returns `404`.

## 5.3 Shapes: msgspec

```python
@dataclass_transform(frozen_default=True, kw_only_default=True, field_specifiers=(msgspec.field,))
class Shape(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True, rename="camel"):
    """Base for every operation input and output."""
```

- msgspec provides strict immutable shapes, rejects unknown fields and distinguishes strings from numbers. Its generated JSON Schema supplies tool and contract forms.
- Decode: JSON bytes or a dict to a checked `Shape`, or `invalid_input` naming the field. Encode: a `Shape` to JSON bytes (`camelCase` and `snake_case` via `rename="camel"`).
- `@dataclass_transform`: tells type checkers every subclass is frozen and keyword-only, as msgspec already does at runtime.
- Constraints: `Annotated[str, Meta(min_length=1, max_length=200)]`. Rule: every operation input and output is a `Shape`.

## 5.4 Roles and access

Each operation declares a permission. Account roles are rows in `user_roles` (§7); the permission map is versioned code. Roles load on every request. Agents receive tool permissions rather than account roles. The operation check, agent effect gate and resource check apply on every path. A missing or unreadable owned record returns `404`. Section 3.4 defines the decision rule.

## 5.5 Logging

- Each operation call and `@observe` step writes:
  - one JSON line to stdout (stdlib `logging`);
  - one `call_log` row (§7), in its own short transaction, so failed calls are logged too.
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
