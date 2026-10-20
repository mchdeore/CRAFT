# 6. Code layout

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP). Tool calls use the Model Context Protocol (MCP).

## 6.1 Layout

```text
app/
  __init__.py          create_app(settings): composes routes, MCP and lifespan
  main.py              app = create_app(Settings.from_env())
  cli.py               version, seed, index, users, missions, provider, operations, call, mcp
  infrastructure/      shared access, settings, database, operations, MCP, provider,
                       processes, logging, requests, errors, shapes and paths
  health/              /health/live, /health/ready
  agent/               runtime, instructions, stream, combined answers and recovery
  accounts/            local accounts and sign-in sessions
  conversations/       owned history and uploaded or generated files
  library/             indexing, search, source reads, citations and version differences
  cube/                mission and parameter reads, similarity and value publication
  approvals/           frozen requests, requester decisions and execution events
  notes/               owner-scoped working notes
  skills/              owned immutable definitions and native agent loading
  tools/               chart rendering, calculation checks and capabilities
  seed/                generated base and demo orchestration
  summarize/           document inspection APIs and /summarize shell
  dev/                 optional /dev sign-in, chat, approval and file page
migrations/versions/   numbered revisions 0001 through 0013
seeds/base/            synthetic accounts, grants and provider Markdown
packages/             standalone prototypes and the lifecycle package
api/                   executable TypeScript contract and examples
docs/                  public documents and build tools
compose.yaml  Dockerfile  docker/initdb.sh  .env.example  pyproject.toml  uv.lock
```

Handlers expose operations. Services coordinate a use case and select short transactions. Storage receives an explicit Session and never commits independently. The optional lifecycle attachment belongs to the application lifespan; it exposes no process-creation route.

The runtime, Python workspace, document and fleet packages are standalone development prototypes. The production application uses its own agent and provider implementations and includes the lifecycle package. The dependency reference owns package and image pins.

## 6.2 Conventions

- Operations use `<feature>.<verb_noun>`; tool names use the function name with lowercase letters, digits and underscores.
- Inputs and outputs use strict msgspec shapes. Private observations expose no route or tool.
- Python follows the configured Pyright, Ruff and formatting rules.
- Dependencies use `uv add` and `uv sync --locked`.
- Source changes use reviewed pull requests. Secrets stay outside tracked source and logs.

## 6.3 Contract adherence

The server matches the route definitions, shapes and event formats in the API contract. Identity, permissions and effect checks apply across HTTP, MCP and checked Python calls. Settings validate at startup and report an invalid field without its value.

## 6.4 Feature boundaries

Handlers declare access, shapes and operations under section 5. The registry supplies HTTP routes, MCP tools and checked Python entry points. Cross-feature calls retain the trusted Caller and resource permissions.

Numbered migrations declare table grants. The early revisions cover accounts, conversations, approvals, library, drafts and files; cube revisions follow, then notes and skills. Seed commands generate labeled data outside migrations.
