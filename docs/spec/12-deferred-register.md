# 12. Deferred register

| What | Now | Deferred | Trigger |
|---|---|---|---|
| Sign-in | Local accounts | Entra ID | An application registration is available |
| Access | Local account roles and operation/resource checks | Expanded governed role mapping | Before real data or larger teams |
| Decisions | The requester approves; the agent never approves | Refusing self-approval, designated approvers, eligibility, escalation, timeouts and request editing | Before real data or larger teams |
| Model access | Key from `.env`, simulated data | Managed identity; data handling agreed | Before real data |
| Agent run | In the chat request | Worker + queue | Runs must survive disconnects or scale |
| Agent hosting | CRAFT backend | Optionally Foundry hosted agents | Hosting adapter generally available |
| Agents | One | Sub-agents, then specialists | CRAFT runs end to end |
| Retrieval | PostgreSQL hybrid | Azure AI Search + semantic ranker | Azure deployment or a real corpus |
| Data | Simulated corpus | Connectors, ingestion, source register | A source is approved |
| Hosting | Docker Compose | Azure Container Apps or App Service, continuous deployment | A proven proof of concept |
| Security | Local only | Security assessment (Protected B), private endpoints, secret scanning | Azure deployment starts |
| Audit | Append-only approval history | Write-once storage, retention, export | Deployment |
| Logs | stdout + `call_log` | OpenTelemetry, Azure Monitor, retention | Deployment |
| Run limits | Starting values | Measured values | Evaluation set exists |
| Confidence calibration | Six evidence inputs and configured gate | Calibrated weights and thresholds | The labelled evaluation set exists |
| Language | English | French + detection | Scope change |
| Code execution | None | Sandbox (hosted agent or Container Apps dynamic sessions) | An analysis needs generated code |
| Load | Not sized | Sized to the load profile | Before sizing infrastructure |
| Database hosting | Local container | Azure Database for PostgreSQL with pgvector, same migrations | Deployment |
| Database logins | Logins created by `initdb.sh` | Logins and grants from infrastructure code; managed identity | Deployment |
| Database recovery | Simulated data, no backups | Managed backups, retention and recovery | Before real data |
| Document permissions | Role on each document | Source-system permissions | Real connectors |

## 12.1 Datacube scope extensions

| What | Now | Deferred | Trigger |
|---|---|---|---|
| Revisions and version matching | Section 8.11 links existing documents; section 8.8 selects dated values | R3 silo document versions, block matching, styled differences and stage baselines | Actual engineering stages and a verified revision fixture set |
| Earned value and current estimate | Section 8.1 covers technical parameters | R4 simulated earned value and one current estimate per mission period | Approved period, currency and evidence rules with finance acceptance fixtures |
| Invoices | Section 8.11 preserves document identity and hashes | R4 source/vendor/record/revision identity and projections linked to updates | Approved simulated invoice fixtures and identity rules |
| Export reports | Section 8.12 permits a bounded JSON workspace view | R5 styled reports, complete packages and governed external destinations | Approved template, claim checks and export policy |
| Cost estimation | Section 8.10 returns parameter analogues and evidence | R4 uncertainty estimates, imputation and back-tested costing | Approved simulated costing corpus, method and back-test targets |
| Narrative and dataset intake | Section 8.16 reads simple workbook tables and typed forms | R2 scanned/narrative extraction, complex tables and static converted datasets | Per-format locator and extraction acceptance gates |
| Category embeddings | Section 8.10 uses numeric distance and section 10 ranks documents | R3 category, silo and stage embedding methods | A labelled similarity evaluation set and permission checks |
| Stage reference list | Section 8.2 uses made-up replaceable reference stages | Actual stages, gate conditions and deliverables supplied by the president | Before R3 engineering history |
| External research onboarding | Section 8.1 uses the controlled simulated corpus | Registered read-only external research sources | Source approval, classification and query-time permissions |
| Risk and anomaly analysis | Section 8.15 researches cited evidence | Severity, detection and governed risk operations | Approved taxonomy, labelled anomalies and action gates |

## 12.2 Datacube extensions

| What | Now | Deferred | Trigger |
|---|---|---|---|
| Mission and silo access | Section 8.17 applies mission, silo and evidence access before ranking and counts | Delegated grant administration and governed roles | Larger teams or real source onboarding |
| Change approval roles | Section 8.17 lets the requester decide and prevents agent decisions | Datacube integration of the expanded policy in the Decisions row | Governed approval policy before real data |
| Mission similarity editing and calibration | Section 8.10 has sealed editable definition versions with frozen scales | Browser editing and calibration of the mission-similarity definition | A labelled analogue evaluation set |
| Invoice identity | Section 8.21 places invoices in R4 | Identity by source, vendor, source record and revision rather than display number | Simulated invoice intake |
| Historical document state | Section 8.8 selects value state by valid and recorded time | Document state on a date and frozen gate baselines | R3 document-version fixtures |
| Financial authority | Section 8.1 uses simulated technical data | Simulated finance extracts with the financial system authoritative | R4 approved read-only finance boundary |
| Estimate selection | Section 8.21 places estimates in R4 | One current estimate per mission period selected by append-only events | Approved period and estimate identity |
| Repository write boundary | Section 8.11 provides no document editing route | Any write adapter for an original repository | Separate source-write policy and approval |
| Capability traceability | Section 8.6 returns evidence and value handles | A complete capability and decision traceability report | R5 audited report fixtures |
| Original-source connectors | Section 8.11 links section 7 original references | Governed read-only repository connectors and availability monitoring | Source onboarding with original access verification |
