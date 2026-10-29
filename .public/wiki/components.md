# Components

## Responsibilities

| Component | Responsibility | Code |
|---|---|---|
| Application | Authenticated chat, saved skills, approvals, document and cube tools, conversation files and PostgreSQL records | `app/` |
| Process lifecycle | Controller-owned child processes and containers, with an optional application lifespan attachment | `app/processes/` |
| Contract | Executable TypeScript forms, routes and examples the product interface compiles against | `app/contract/` |
| Product interface | The signed-in web interface, built by the partner team in the separate `craft-frontend` repository | Not in this repository |
| JupyterLab workspace | The personal workspace image, its `craft` package, built-in skills, the activity log and the notebook-run steps; JupyterHub starts one per person | `workspace/`, `deploy/jupyterhub/` |
| Deployment | One Helm chart for the API, workers, craft-id and PostgreSQL on the local k3s cluster | `deploy/` |
| Workers | Background jobs from the PostgreSQL queue, including chat turns ([background jobs](processes.md#background-jobs)) | `craft worker` in `app/` |

## Application boundaries

Handlers expose the application boundary. Services coordinate a use case. Storage functions use the transaction supplied by that use case. Shared access, request, operation and provider behavior lives in `app/infrastructure/`. The [code layout](development.md) owns the detailed application rules.

The [operation contract](api-contract.md) owns the public forms. A Python package exposes an import interface; its presence in the development environment does not add an application route.

The application agent uses `app/agent/` and PostgreSQL state. Backend chart rendering uses `app/tools/`; it does not execute a Python cell. The production image includes the lifecycle code in `app/processes/` and omits the standalone Python workspace package.

## Connections

| # | Sender | Receiver | Protocol | Carries |
|---|---|---|---|---|
| 1 | Browser | Backend | HTTP + JSON under `/api` | Sign-in, conversations, tools list, library, cube, drafts, approvals |
| 2 | Backend | Browser | AG-UI events over SSE (one-way server stream) | One agent run, read from the run's stored events: text, tool progress, approval pause, answer metadata |
| 3 | Agent runtime (on `worker-agent`) | Backend | MCP over HTTP at `/mcp` + run token | Tool calls only |
| 4 | Agent runtime | Foundry | Responses API | Model and embedding calls |
| 5 | Local MCP application | Backend | MCP over standard input and output through `craft mcp --as <username>` | Tool calls only |
| 6 | Backend | Document provider | Python functions over the provider folder ([document provider](datacube.md#document-provider)) | Changes and original files |
| 7 | Backend | Workers | Jobs in PostgreSQL (`LISTEN`/`NOTIFY`) | Chat turns and other background jobs; run events back to the backend |
| 8 | Backend | JupyterHub | Hub REST API + one service token | Start, stop and status of a person's workspace |
| 9 | Backend | Person's JupyterLab | Jupyter Contents API and the notebook control server, through the Hub's proxy | The agent's workspace reads and approved writes |
| 10 | Interactive worker | Kubernetes API | Jobs, with a service account limited to them | Unattended notebook runs on the person's volume |

- One entry point: every route and tool is an `@operation` handler ([backend conventions](development.md#backend-conventions)). A person, a script and the agent get the same checks and log line.
- Services aren't agent-only: every tool is also a route.
- Identity comes from the server: the cookie (1), the run token (3) or the account named on the command line (5), never a request body.
- No transaction stays open across a model call, a search or a wait for a person.

## Related topics

See [runtime](runtime.md), [workspace](workspace.md), [documents](documents.md), [development](development.md) and [operations](development.md).
