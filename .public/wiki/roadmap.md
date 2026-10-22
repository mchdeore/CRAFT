# Roadmap

What CRAFT does now, what is deferred until a trigger is met, and the longer-term vision. This page is the one place for scope; plans and pull requests link here.

## Now

### Built

| Area | What works | Foundation |
|---|---|---|
| Platform | Settings, database initialization, short transactions, request IDs, logs and health | Locked dependencies and the non-root application image |
| Records | Accounts, conversations, runs, approvals, library, drafts, files, notes and skills | Numbered migrations with explicit grants |
| Operations | One registry for HTTP routes, MCP tools and checked Python calls, with access checks | Trusted caller context and strict shapes |
| Documents | Local provider with stable external-style IDs, incremental ingestion, office conversion, indexing, search, citations and version differences; OCR for scanned PDFs and images as queued jobs | Immutable source versions and mission grants |
| Datacube | Missions, parameters, values, facets, comparisons, similarity and approved value publication | Library evidence and requester approval |
| Agent | Model adapter, chat stream, saved history, approval recovery, working notes, saved skills, chart and calculation tools | Native HTTP MCP, server-owned state and requester decisions |
| Chat | Rename, grouping, archive and unarchive | Owned conversations |
| Workspace | A personal JupyterLab beside chat, the `craft` package, analysis and graphing helpers, editable local copies, and workspace skills | Owned containers and workspace tokens |
| Background work | A durable worker queue (`craft worker`) for indexing and OCR jobs | Valkey and the `worker` Compose profile |
| Demo | Generated demo set, a saved seed snapshot and `scripts/demo` | Provider data and the API contract |

### Next

1. **Multilingual documents**: language detection and translation into a separate artifact, such as a French copy, without changing the source. It starts with an endpoint smoke test.
2. **Azure Container Apps deployment**: one low-cost hosted environment updated from local development through a repeatable pipeline.

## Deferred

Each row stays as it is until its trigger happens.

| What | Now | Deferred | Trigger |
|---|---|---|---|
| Sign-in | Local accounts | Entra ID | An application registration is available |
| Access | Local account roles and operation/resource checks | Expanded governed role mapping | Before real data or larger teams |
| Decisions | The requester approves; the agent never approves | Refusing self-approval, designated approvers, eligibility, escalation, timeouts and request editing | Before real data or larger teams |
| Model access | Key from `.env`, simulated data | Managed identity; data handling agreed | Before real data |
| Agent run | In the chat request; indexing and OCR use the worker queue | Agent runs on the worker | Runs must survive disconnects or scale |
| Agent hosting | CRAFT backend | Optionally Foundry hosted agents | Hosting adapter generally available |
| Agents | One | Sub-agents, then specialists | CRAFT runs end to end |
| Retrieval | PostgreSQL hybrid | Azure AI Search with the semantic ranker | Azure deployment or a real corpus |
| Data | Simulated corpus | Connectors, ingestion, source register | A source is approved |
| Document analysis | Text extraction and OCR | Content Understanding analyzers that feed the datacube | Promoted after multilingual documents |
| Hosting | Docker Compose | Azure Container Apps with continuous deployment | Next (see above) |
| Security | Local only | Security assessment (Protected B), private endpoints, secret scanning | Azure deployment starts |
| Audit | Append-only approval history | Write-once storage, retention, export | Deployment |
| Logs | stdout and `call_log` | OpenTelemetry, Azure Monitor, retention | Deployment |
| Run limits | Starting values | Measured values | Evaluation set exists |
| Confidence calibration | Six evidence inputs and configured gate | Calibrated weights and thresholds | The labelled evaluation set exists |
| Language | English | French and detection | Next (see above) |
| Code execution | Approved `run_code` in the owner's JupyterLab | Hosted sandbox (Container Apps dynamic sessions) | Azure deployment |
| Load | Not sized | Sized to the load profile | Before sizing infrastructure |
| Database hosting | Local container | Azure Database for PostgreSQL with pgvector, same migrations | Deployment |
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
