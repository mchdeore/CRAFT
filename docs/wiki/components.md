# Components

## 1. Responsibilities

| Component | Responsibility | Code |
|---|---|---|
| Application | Authenticated routes, operations and PostgreSQL data | `app/` |
| Runtime library | Tool execution, run state and memory decisions | `packages/runtime/` |
| Python workspace | Restricted kernel sessions and managed artifacts | `packages/python_workspace/src/craft_workspace/` |
| Analysis library | Parameter comparison, forecasts, risk and charts | `packages/python_workspace/src/craft_analysis/` |
| Document library | Source versions, representations, evidence and simulated provider reconciliation | `packages/data_provider/` |
| Model job library | Local job queue, provider requests and reviewed research artifacts | `packages/provider_fleet/` |
| Audio library | Immutable audio/transcript versions and reviewed goal suggestions | `packages/audio_inbox/` |
| Research library | Snapshot catalog, chronological baselines and exact payoff calculations | `packages/research_data/` |

## 2. Application boundaries

Handlers expose the application boundary. Services coordinate a use case. Storage functions use the transaction supplied by that use case. Shared access, request, operation and provider behavior lives in `app/infrastructure/`. The [code layout](../spec/06-code-layout.md) owns the detailed application rules.

The [operation contract](../API-Contract.md) owns the public forms. A Python package exposes an import interface; its presence in the development environment does not add an application route.

## 3. Related topics

See [runtime](runtime.md), [workspace](python.md), [documents](documents.md), [jobs and audio](jobs-and-audio.md), [development](development.md) and [operations](../spec/05-operations-and-logging.md).
