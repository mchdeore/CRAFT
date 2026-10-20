# 1. Scope

<p class="meta">Version 1.0 | 14 October 2026</p>

This document defines CRAFT. Code and plans follow it.

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP) with JavaScript Object Notation (JSON). The Model Context Protocol (MCP) carries tool calls. Agent-User Interaction (AG-UI) events use Server-Sent Events (SSE). Identifiers are universally unique identifiers (UUIDs), and times use Coordinated Universal Time (UTC). The command-line interface (CLI) calls the same operations. Source fingerprints use the 256-bit Secure Hash Algorithm (SHA-256).

This specification describes available application behavior.

Security requirements must meet the president's demands.

The conversation owns uploaded and generated files under section 7. The application exposes document, cube, chart and calculation operations through the authorized registry. The standalone Python workspace is described in the wiki.

| Area | Behavior |
|---|---|
| Users | Local accounts and server-owned sessions |
| Data | Simulated, labeled corpus |
| Runs on | Local backend and Docker Compose |
| Agent | One agent, run inside the CRAFT backend |
| Approvals | The requester decides effectful agent actions; the agent never decides |

- Stack:
  - Python, FastAPI, msgspec, PostgreSQL with pgvector, SQLAlchemy
  - Microsoft Agent Framework, models from Microsoft Foundry
  - the front end, built against the `api/` contract
- Protocols: HTTP with JSON for ordinary calls, AG-UI for the chat stream, and MCP for agent tool calls.

The `/summarize` page reads one exact library version or one owned simulated upload. It returns cited extracts and filing suggestions without changing the corpus, cube or conversation storage. Upload originals stay in request and browser memory.
