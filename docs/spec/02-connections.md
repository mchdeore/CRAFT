# 2. Connections

Hypertext Transfer Protocol (HTTP) carries JavaScript Object Notation (JSON). Agent-User Interaction (AG-UI) events use Server-Sent Events (SSE). Tool calls use the Model Context Protocol (MCP). The application programming interface is the API.

| # | Sender | Receiver | Protocol | Carries |
|---|---|---|---|---|
| 1 | Browser | Backend | HTTP + JSON under `/api` | Sign-in, conversations, tools list, library, cube, drafts, approvals |
| 2 | Backend | Browser | AG-UI events over SSE (one-way server stream) | One agent run: text, tool progress, approval pause, answer metadata |
| 3 | Agent runtime | Backend | MCP over HTTP at `/mcp` + run token | Tool calls only |
| 4 | Agent runtime | Foundry | Responses API | Model and embedding calls |
| 5 | Local MCP application | Backend | MCP over standard input and output through `craft mcp --as <username>` | Tool calls only |
| 6 | Backend | Document provider | Python functions over the provider folder (section 8.3) | Changes and original files |

- One entry point: every route and tool is an `@operation` handler (§5). A person, a script and the agent get the same checks and log line.
- Services aren't agent-only: every tool is also a route.
- Identity comes from the server: the cookie (1), the run token (3) or the account named on the command line (5), never a request body.
- No transaction stays open across a model call, a search or a wait for a person.

API forms are enforced by contract adherence.
