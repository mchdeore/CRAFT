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
| Workspace | A personal JupyterLab beside chat that JupyterHub starts when the person or the agent needs it and stops after an hour idle, keeping their files; the `craft` package (`import craft` is the only import a notebook needs; `craft.check()` lists missing tools), analysis and graphing helpers, editable local copies, and workspace skills; an activity log of the person's own saves, kernel actions and cell runs, with user, path and timing and never content | JupyterHub on the local cluster, one volume per person, craft-id sign-in and workspace tokens |
| Charts, notebooks and Office files | Charts the agent makes, notebooks co-edited with the agent, Word and PowerPoint output with a page preview, and unattended notebook runs: on the cluster a Kubernetes Job with no network, the notebook's folder read-only and results moved beside it without overwriting | Workspace image with LibreOffice and Poppler |
| Background work | Jobs on PostgreSQL with one worker per queue (agent, interactive, bulk), retries for transient failures, crash recovery and job routes; a job is declared on its operation; wait and run times are measured (`craft queues report`) | Procrastinate; no separate queue service |
| Dataset | A frozen library index and datacube, loaded by `scripts/seed` with no model calls | The private frozen set and the numbered migrations |
| Local cluster | The API, the workers, craft-id and PostgreSQL run from one Helm chart on Colima's k3s, with JupyterHub from its own unmodified chart beside it; network policies limit what each pod reaches ([deploy/README.md](https://github.com/mchdeore/CRAFT/blob/main/deploy/README.md)) | The same charts the hosted VM will use |
| Interface | No interface in this repository. The product interface is the separate `craft-frontend` repository | It compiles against `app/contract/` |

### Next

Each item is planned and has a trigger met or close. The maintainer chooses the order; the list is not a sequence. Everything here is sized as a proof of concept: 5 people at once and 20 accounts in all.

In progress:

- **Database backup**: a nightly `pg_dump` CronJob writes to a folder on the node's own disk and keeps the newest 14 dumps; `scripts/backup-check` restores the newest into a throwaway database and prints row counts. It covers a bad migration or a wrong delete, not a lost disk.
- **Background jobs, extended**: schedules, progress a person can see, a notification to the owner when a job or run ends, and longer agent tasks that run in the background.
- **Traffic counting**: every request records bytes in and out, its operation and its caller, counted once for all routes.

Planned:

- **One way to run**: Kubernetes now runs the workspaces and notebook runs locally, so the Docker runtime paths are deleted next: the Docker sandbox, Compose, the Docker SDK and the `docker` test mark. The Dockerfiles stay to build the images.
- **Verification**: one pass checks that the Kubernetes work fits together on the local cluster, and the whole stack is checked start to finish on one machine, with real model calls capped at $10 in total.
- **Outside MCP clients sign in**: a person makes a personal token after signing in with craft-id, and an MCP client such as Claude Desktop or an IDE uses `/mcp` as that person, limited to read tools. Today only the agent's run token reaches `/mcp`, and `craft mcp --as <username>` is a local developer tool.
- **Standing approvals**: the requester can approve an effectful call once, for the conversation or for a day, for every operation; this replaces the notebook-only grant.
- **Workspace index and history**: a small index of each person's files (tree, outline, full-text search) that the agent can use while the workspace is stopped, and a history of every version by content hash.
- **Sharing**: up to 5 people in one conversation with one agent, one turn at a time, and read-only sharing of a workspace file.
- **Workspace graph**: people link their workspace documents with relations they choose, returned as one graph.
- **Datacube v1 written down**: the frozen cube documented as `cube-v1`, with checksums and expected counts so a restore can be checked, and a second copy of the frozen set.

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
| Data | Simulated corpus and a LiveLink-shaped test adapter | Real LiveLink, then SharePoint and OneDrive through Microsoft Graph | Access and a service account are granted |
| Document analysis | Text extraction and optional OCR over the frozen dataset | Structured table intake: document-analysis rows become datacube candidates a person approves | The dataset is unfrozen and reindexed |
| Contract publishing | `app/contract/` in this repository, which `craft-frontend` compiles against | Published to the partner team's contract repository | The partner team's integration needs it |
| Hosting | A local k3s cluster from one Helm chart, with JupyterHub for the workspaces; Docker Compose still runs the API and workers, without workspaces, until it is removed (next) | One small Azure VM running k3s with the same charts, then AKS, a registry and continuous deployment | The VM: everything else is tested on the local cluster. Past the VM: a concrete reason to expand |
| Security | Local only | Security assessment (Protected B), private endpoints, secret scanning | Before production data |
| Audit | Append-only approval history | Write-once storage, retention, export | Deployment |
| Logs | stdout, `call_log` and the workspace activity log in each workspace pod's output | OpenTelemetry, Azure Monitor, retention | Deployment |
| Run limits | Starting values | Measured values | Evaluation set exists |
| Confidence calibration | Six evidence inputs and configured gate | Calibrated weights and thresholds | The labelled evaluation set exists |
| Code execution | Approved `run_code` in the owner's JupyterLab; notebook runs as Kubernetes Jobs on the cluster, and in restricted Docker containers under Compose | A process limit for run pods, which Kubernetes sets for the whole node | The VM |
| Load | A proof of concept for 5 people at once and 20 accounts; each queue takes up to 50 jobs at once and its wait and run times are measured | Sized to the measured load | Before sizing infrastructure |
| Database hosting | Local container or a PostgreSQL pod in the cluster | Azure Database for PostgreSQL with pgvector, same migrations | A concrete reason to expand past the VM |
| Database logins | Logins created by `initdb.sh` | Logins and grants from infrastructure code; managed identity | Deployment |
| Database recovery | Simulated data, no backups; a nightly dump to the node's disk is in progress | A copy off the disk, then managed backups, retention and recovery | Real data or people's work on the VM |
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
