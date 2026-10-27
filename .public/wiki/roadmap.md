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
| Demo | Generated demo set, a saved seed snapshot and `scripts/demo` with the backend's `/dev` page | Provider data and the API contract |
| Interface | No interface in this repository. The product interface is the separate `craft-frontend` repository | It compiles against `app/contract/` |

### Next

Each item is planned and has a trigger met or close. The maintainer chooses the order; the list is not a sequence.

- **Background jobs on PostgreSQL**: the worker queue moves from Valkey into PostgreSQL, with retries, priorities and a job history. Valkey is removed.
- **One API contract with the product interface**: `app/contract/` is the single source, published to the partner team's contract repository, with sign-in and the routes the product interface calls.
- **Document extraction bake-off**: compare the OCR and document-analysis models on the same files, then move OCR from a preview model to a generally available one.
- **Sign-in shaped like Entra ID**: craft-app signs people in with OpenID Connect through a small identity provider that CRAFT runs and that mirrors Entra ID. Passwords no longer reach craft-app, and CRAFT keeps deciding who has access.
- **Azure deployment**: staging, then production, on Azure Container Apps with Azure Database for PostgreSQL, Azure Files for the document lake, Key Vault, and Azure Front Door on the project's domain.
- **Multilingual documents**: language detection, and translation into a separate file, such as a French copy, without changing the source. Then each person's language preferences.
- **Charts, notebooks and Office files**: charts the agent makes, notebooks worked on with the agent, Word and PowerPoint output, and notebook runs as sandboxed background jobs.
- **Provider adapters and CRAFT revisions**: one provider interface with several adapters, a LiveLink-shaped test server, and CRAFT-owned revisions of documents (full copies that point at their source version), exported at any point in time and pushed back only after approval.

## Deferred

Each row stays as it is until its trigger happens.

| What | Now | Deferred | Trigger |
|---|---|---|---|
| Sign-in | Local accounts; next, OpenID Connect through a CRAFT-run identity provider | Entra ID, with the same settings shape | An application registration is available |
| Access | Local account roles and operation/resource checks | Expanded governed role mapping; Entra groups or app roles | Before real data or larger teams |
| Decisions | The requester approves; the agent never approves | Refusing self-approval, designated approvers, eligibility, escalation, timeouts and request editing | Before real data or larger teams |
| Model access | Key from `.env`, simulated data | Managed identity; data handling agreed | Before real data |
| Agent run | In the chat request (capped at the hosting timeout once on Azure); indexing and OCR use the worker queue | Detachable agent runs on the worker | Runs must survive disconnects or scale |
| Agent hosting | CRAFT backend | Optionally Foundry hosted agents | Hosting adapter generally available |
| Agents | One | Sub-agents, then specialists | CRAFT runs end to end |
| Model routing | Fixed main and check models | A model router | Measured cost or quality need |
| Retrieval | PostgreSQL hybrid | Azure AI Search with the semantic ranker | A real corpus outgrows PostgreSQL |
| Data | Simulated corpus; next, a LiveLink-shaped test server | Real LiveLink, then SharePoint and OneDrive through Microsoft Graph | Access and a service account are granted |
| Document analysis | Text extraction and OCR; next, the extraction bake-off | Structured table intake: document-analysis rows become datacube candidates a person approves | The bake-off shows it beats today's table parsing |
| Hosting | Docker Compose | Azure Container Apps with continuous deployment | Next (see above) |
| Security | Local only | Security assessment (Protected B), private endpoints, secret scanning | Before production data |
| Audit | Append-only approval history | Write-once storage, retention, export | Deployment |
| Logs | stdout and `call_log` | OpenTelemetry, Azure Monitor, retention | Deployment |
| Run limits | Starting values | Measured values | Evaluation set exists |
| Confidence calibration | Six evidence inputs and configured gate | Calibrated weights and thresholds | The labelled evaluation set exists |
| Code execution | Approved `run_code` in the owner's JupyterLab | Hosted sandbox (Container Apps dynamic sessions) | Azure deployment and a sessions trial |
| Load | Not sized | Sized to the load profile | Before sizing infrastructure |
| Database hosting | Local container | Azure Database for PostgreSQL with pgvector, same migrations | Next (see above) |
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
