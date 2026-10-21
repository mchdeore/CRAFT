# 11. Build order

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP) and JavaScript Object Notation (JSON). Agent-User Interaction (AG-UI) carries the chat stream.

## 11.1 Application dependencies

The following order describes the implemented component dependencies.

| Order | Components | Foundation |
|---|---|---|
| 1 | Settings, database initialization, transactions, request IDs, logs and health | Locked runtime and application image |
| 2 | Account, conversation, run, approval, library, draft and file tables | Migrations 0001 through 0008 and explicit grants |
| 3 | Operation registry, access checks, HTTP and MCP adapters | Trusted caller context and strict shapes |
| 4 | Local provider, office conversion, indexing, search and citations | Immutable source versions and mission grants |
| 5 | Missions, parameters, values, facets, comparisons and similarity | Migrations 0009 through 0011 and library evidence |
| 6 | Model adapter, agent stream, saved history and approval recovery | Native HTTP MCP, server-owned state and requester decisions |
| 7 | Working notes, saved skills, chart and calculation tools | Owned records, exact skill revisions and existing operations |
| 8 | Base and generated demo sets, document summary and browser shells | Checked handlers, provider data and the API contract |

Notes use migration 0012 and skills use migration 0013. Seed commands run separately from migrations. The optional lifecycle package attaches to the application lifespan. Standalone runtime and workspace packages have their own component boundaries.

## 11.2 Running the application

The dependency reference owns setup, image builds, seed/index commands and verification commands. The API contract owns executable routes and examples. The built browser shells are `/dev` when enabled and `/summarize`; the authenticated frontend runs separately against those APIs.
