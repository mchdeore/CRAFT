# CRAFT

CRAFT answers questions about project documents with cited sources and maintains an engineering datacube. Its signed-in interface puts chat beside a personal JupyterLab workspace, with a tools reference and saved skills. Conversations keep answers, evidence, approvals and uploaded or generated files. The data is a simulated, labelled corpus.

This wiki is the source of truth for what CRAFT is and how it works. It is published at [mchdeore.github.io/CRAFT](https://mchdeore.github.io/CRAFT/) and is readable whether or not the app is running. Its source is `.public/wiki/` in the repository. The code is at [github.com/mchdeore/CRAFT](https://github.com/mchdeore/CRAFT).

## At a glance

| Area | Behavior |
|---|---|
| Users | Local accounts and server-owned sessions |
| Data | Simulated, labelled corpus |
| Runs on | A local k3s cluster from one Helm chart, with JupyterHub for the workspaces; Docker Compose still runs the backend without workspaces until it is removed |
| Agent | One agent, run inside the CRAFT backend |
| Approvals | The requester decides effectful agent actions; the agent never decides |
| Stack | Python, FastAPI, msgspec, PostgreSQL with pgvector, SQLAlchemy; Microsoft Agent Framework with models from Microsoft Foundry; the product interface is the separate `craft-frontend` repository, built against the API contract |
| Protocols | HTTP with JSON for ordinary calls, AG-UI over Server-Sent Events for the chat stream, MCP for agent tool calls |

## Pages

- [Roadmap](roadmap.md): Now, Deferred and Vision.
- [Development](development.md): layout, setup, checks and backend conventions.
- [Components](components.md): what each part does and how they connect.
- [Agent](agent.md): one request, tools over MCP, approvals, safety, the chat stream, models and confidence.
- [Agent runtime](runtime.md): application runs, saved skills and approvals.
- [Process lifecycle](processes.md): owned services, readiness and shutdown.
- [Workspace](workspace.md): shared files, notebooks, source copies and native Python tools.
- [Identity and workspace ownership](identity.md): users, credentials, runtime boundaries and the table reference.
- [Accounts and access](access.md): accounts, sessions, browser and MCP access, workspace tokens.
- [Documents and evidence](documents.md): identity, versions, extraction, relationships and retrieval.
- [Engineering datacube](datacube.md): provider, library, missions, parameters, values, intake, and the database.
- [API contract](api-contract.md): rules, routes, agent tools and examples.
- [Dependencies](dependencies.md): outside services and why CRAFT uses them.
- [Glossary](glossary.md)

To change this wiki, follow [editing](EDITING.md).
