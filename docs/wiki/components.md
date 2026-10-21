# Components

## 1. Responsibilities

| Component | Responsibility | Code |
|---|---|---|
| Application | Authenticated chat, saved skills, approvals, document and cube tools, conversation files and PostgreSQL records | `app/` |
| Runtime prototype | Standalone tool execution, run state and memory decisions | `packages/runtime/` |
| Python workspace prototype | Standalone restricted kernel sessions and managed artifacts | `packages/python_workspace/src/craft_workspace/` |
| Analysis prototype | Pure parameter comparison, forecasts, risk and charts over supplied tables | `packages/python_workspace/src/craft_analysis/` |
| Process lifecycle | Controller-owned child processes and containers, with an optional application lifespan attachment | `packages/process_manager/` |

## 2. Application boundaries

Handlers expose the application boundary. Services coordinate a use case. Storage functions use the transaction supplied by that use case. Shared access, request, operation and provider behavior lives in `app/infrastructure/`. The [code layout](../spec/06-code-layout.md) owns the detailed application rules.

The [operation contract](../API-Contract.md) owns the public forms. A Python package exposes an import interface; its presence in the development environment does not add an application route.

The application agent uses `app/agent/` and PostgreSQL state. It does not invoke the runtime or Python workspace prototypes. Backend chart rendering uses `app/tools/`; it does not execute a Python cell. The production image includes the lifecycle package and omits the standalone development packages.


## 3. Related topics

See [runtime](runtime.md), [workspace](python.md), [documents](documents.md), [development](development.md) and [operations](../spec/05-operations-and-logging.md).
