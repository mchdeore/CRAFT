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
| Document permissions | Mission grants (section 8.15) | Source-system permissions | Real connectors |
