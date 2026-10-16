# 6. Code layout

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP). Tool calls use the Model Context Protocol (MCP).

## 6.1 Layout

```text
app/
  __init__.py          create_app(settings): composes everything; no work at import
  main.py              app = create_app(Settings.from_env())
  cli.py               S0: craft --version; later slices: seed, users, operations, call
  infrastructure/      shared; never imports a feature
    config/ database/ operations/ mcp/ provider/ access.py logging.py requests.py errors.py shapes.py paths.py
  health/              /health/live, /health/ready
  agent/               runtime, instructions.md, chat + tools-list operations
  <feature>/           accounts, conversations, library, cube, drafts, approvals
    handlers.py        shapes + operations (the only entry points)
    service.py         rules, only when there's logic beyond one query
    storage.py         tables and queries with an explicit Session; never commits
  dev/                 dev.html: temporary development page, S2 through S4, deleted in S5
migrations/versions/   NNNN_<verb>_<thing>.py
seeds/<set>/           seed data + manifest (base first)
api/                   TypeScript contract + examples/
docs/                  public documents and build tools
compose.yaml  Dockerfile  docker/initdb.sh  .env.example  .gitattributes  pyproject.toml  uv.lock
```

Create only the files a feature needs. S0 provides configuration, the database pool and transaction helper, health, request identifiers, error mapping and logging. Feature handlers, the operation registry, account sessions and the runtime follow the paths in this layout.

## 6.2 Conventions

- Code: reads like a story (short functions, one level of detail, no clever code); start with a plain function, add a decorator for shared behavior, and add a `Protocol` only when two real implementations exist; enforce a rule with a database constraint first, then a type, validation or an assertion when needed.
- Python: standard Python style, Pyright, Ruff format at 100 columns, docstrings on public functions, narrow `except`, every `noqa` / `type: ignore` has a reason.
- Names: operation `<feature>.<verb_noun>` (`library.get_document`), tool `<verb_noun>`, input `VerbNoun`, output `Noun`, errors `snake_case`, names read as sentences.
- Dependencies: `uv add`, `uv sync --locked`, never `pip install`. A version changes only in a pull request with verified compatibility.
- Git uses one long-lived branch, `main`, and reviewed pull requests. Merge and branch cleanup require explicit authorization. A tag marks each milestone.
- No secrets in code, config, logs, tickets or PRs.

## 6.3 Contract adherence

The server matches the route definitions, shapes and event formats. Identity, permissions and approval requirements apply across HTTP, MCP and Python calls.

## 6.4 Settings

Settings are validated at startup. An invalid setting stops startup and names the field, never its value.

## 6.5 Adding a feature

Handlers declare shapes, access and operations under section 5. The registry supplies HTTP routes, MCP tools and Python entry points; request middleware stays outermost. Services select short transactions and pass the Session to storage. Cross-feature calls enter through handlers with the trusted Caller and resource permissions.

Schema additions use explicit numbered revisions with grants per table. Eight early application revisions include workspace files before the datacube revisions. The seed loader is idempotent and supplies labelled simulated data. Features add only the files they need.
