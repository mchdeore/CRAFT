# Roadmap

What CRAFT does now, what is deferred until a trigger is met, and the longer-term vision. This page is the one place for scope; plans and pull requests link here.

## Now

### Built

| Area | What works | Foundation |
|---|---|---|
| Sign-in | OpenID Connect code flow through craft-id, a CRAFT-run identity provider shaped like Entra ID; craft-app never sees a password, and CRAFT approves who gets in | Same flow, claims and settings shape as Entra ID |
| Platform | Settings, database initialization, short transactions, request IDs, logs and health | Locked dependencies and the non-root application image |
| Records | Accounts, conversations, runs, approvals, library, drafts, files, notes and skills | Numbered migrations with explicit grants |
| Operations | One registry for HTTP routes, MCP tools and checked Python calls, with access checks | Trusted caller context and strict shapes |
| Documents | Local provider with stable external-style IDs, office conversion for file inspection, search, citations and version differences over a frozen library index; one provider interface with a simulated adapter and a LiveLink-shaped test adapter (`CRAFT_PROVIDER`), and CRAFT-owned revisions of documents | Immutable source versions and mission grants |
| Datacube | Missions, parameters, values, facets, comparisons, similarity and approved value publication | Library evidence and requester approval |
| Agent | Model adapter, chat stream, saved history, approval recovery, working notes, saved skills, chart and calculation tools; turns run on the agent worker and survive a closed page or an app restart | Native HTTP MCP, server-owned state and requester decisions |
| Chat | Rename, grouping, archive and unarchive | Owned conversations |
| Languages | Each person picks English or French replies; French replies show the English originals of what they cite | `PATCH /api/me` |
| Workspace | A personal JupyterLab beside chat that JupyterHub starts when the person or the agent needs it and stops after an hour idle, keeping their files; the `craft` package (`import craft` is the only import a notebook needs; `craft.check()` lists missing tools), analysis and graphing helpers, editable local copies, and workspace skills | JupyterHub on the local cluster, one volume per person, craft-id sign-in and workspace tokens |
| Charts, notebooks and Office files | Charts the agent makes, notebooks co-edited with the agent, Word and PowerPoint output with a page preview, and notebook runs as sandboxed background jobs | Workspace image with LibreOffice and Poppler |
| Background work | Jobs on PostgreSQL with three workers (agent, interactive, bulk), retries for transient failures, crash recovery and job routes; a job is declared on its operation | Procrastinate; no separate queue service |
| Dataset | A frozen library index and datacube, loaded by `scripts/seed` with no model calls | The private frozen set and the numbered migrations |
| Local cluster | The API, the three workers, craft-id and PostgreSQL run from one Helm chart on Colima's k3s, with JupyterHub from its own unmodified chart beside it ([deploy/README.md](../../deploy/README.md)) | The same charts the hosted VM will use |
| Interface | No interface in this repository. The product interface is the separate `craft-frontend` repository | It compiles against `app/contract/` |

### Next

Each item is planned and has a trigger met or close. The maintainer chooses the order; the list is not a sequence.

- **One API contract with the product interface**: `app/contract/` is the single source, published to the partner team's contract repository, with sign-in and the routes the product interface calls.
- **Workspace activity logging**: a person's own JupyterLab saves, kernel starts and cell runs are logged with user, path and timing, joined to CRAFT's call log, never with code, outputs or content.
- **Notebook runs as Kubernetes Jobs**: an unattended notebook run becomes a Kubernetes Job with no network, a read-only root and the person's files mounted read-only; cancelling deletes the Job.
- **One small Azure VM**: a cheap proof of concept on one VM running k3s with the same Helm chart, under the project's domain. AKS, a registry, managed databases, Azure Files, Front Door, Key Vault and CI wait until there is a concrete reason.
- **One way to run**: once Kubernetes works locally and on the VM, the Docker runtime paths and Compose are removed.
- **End-to-end verification**: the whole stack checked start to finish on one machine, with real model calls capped at $10 in total. After the Kubernetes work, one more pass checks that it fits together locally and on the VM.
- **Outside MCP clients sign in**: an MCP client such as Claude Desktop or an IDE uses `/mcp` as the person who signed in, limited to read tools. Today only the agent's run token reaches `/mcp`, and `craft mcp --as <username>` is a local developer tool. Waiting on the maintainer's choice between OAuth through craft-id and a personal token.

## Deferred

Each row stays as it is until its trigger happens. Not planned: moving sign-in to Entra ID itself, because craft-id is permanent, and an extraction model bake-off, because the dataset is frozen.

| What | Now | Deferred | Trigger |
|---|---|---|---|
| Access | Local account roles and operation/resource checks | Expanded governed role mapping | Before real data or larger teams |
| Decisions | The requester approves; the agent never approves | Refusing self-approval, designated approvers, eligibility, escalation, timeouts and request editing | Before real data or larger teams |
| Model access | Key from `.env`, simulated data | Managed identity; data handling agreed | Before real data |
| Agent hosting | CRAFT backend | Optionally Foundry hosted agents | Hosting adapter generally available |
| Agents | One | Sub-agents, then specialists | CRAFT runs end to end |
| Model routing | Fixed main and check models | A model router | Measured cost or quality need |
| Retrieval | PostgreSQL hybrid | Azure AI Search with the semantic ranker | A real corpus outgrows PostgreSQL |
| Data | Simulated corpus; next, a LiveLink-shaped test server | Real LiveLink, then SharePoint and OneDrive through Microsoft Graph | Access and a service account are granted |
| Document analysis | Text extraction and optional OCR over the frozen dataset | Structured table intake: document-analysis rows become datacube candidates a person approves | The dataset is unfrozen and reindexed |
| Hosting | Docker Compose for the API and workers, and a local k3s cluster that also runs the workspaces | One small VM running k3s (next), then AKS, a registry and continuous deployment | A concrete reason to expand past the VM |
| Security | Local only | Security assessment (Protected B), private endpoints, secret scanning | Before production data |
| Audit | Append-only approval history | Write-once storage, retention, export | Deployment |
| Logs | stdout and `call_log` | OpenTelemetry, Azure Monitor, retention | Deployment |
| Run limits | Starting values | Measured values | Evaluation set exists |
| Confidence calibration | Six evidence inputs and configured gate | Calibrated weights and thresholds | The labelled evaluation set exists |
| Code execution | Approved `run_code` in the owner's JupyterLab; notebook jobs in Docker containers | Notebook jobs as Kubernetes Jobs (next) | Met: workspaces run through JupyterHub |
| Load | Not sized | Sized to the load profile | Before sizing infrastructure |
| Database hosting | Local container or a PostgreSQL pod in the cluster | Azure Database for PostgreSQL with pgvector, same migrations | A concrete reason to expand past the VM |
| Database logins | Logins created by `initdb.sh` | Logins and grants from infrastructure code; managed identity | Deployment |
| Database recovery | Simulated data, no backups | Managed backups, retention and recovery | Before real data |
| Document permissions | Mission grants ([access](datacube.md#access)) | Source-system permissions | Real connectors |

## Vision

- Shared documents:
  - Markdown sections; every save is an immutable revision (the `drafts` pattern).
  - Section-level diffs between revisions, with the author of each change.
  - Small live events ("v6 saved") on the chat's Server-Sent Events stream. No keystroke co-editing.
  - Accepting or rejecting section changes creates a new revision. A stale base returns `409`.
  - AI edits are candidate changes returned as a diff (`documents.propose_change`); accepting one is a person's action.
  - Export uses Pandoc and a document template to create `.docx`, recording the template version and output hash. Pandoc runs as an external tool under the GNU General Public License.
- Finance forecasting: mission cost estimates from past missions, with evidence and approval before use.
- Excel add-in: another front end using the same HTTP routes.
