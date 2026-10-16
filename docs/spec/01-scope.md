# 1. Scope

<p class="meta">Version 1.0 | 14 October 2026</p>

This document defines CRAFT. Code and plans follow it.

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP) with JavaScript Object Notation (JSON). The Model Context Protocol (MCP) carries tool calls. Agent-User Interaction (AG-UI) events use Server-Sent Events (SSE). Identifiers are universally unique identifiers (UUIDs), and times use Coordinated Universal Time (UTC). The command-line interface (CLI) calls the same operations. Source fingerprints use the 256-bit Secure Hash Algorithm (SHA-256).

Now identifies the proof-of-concept scope. Deferred items reopen at their stated trigger. Section 14 describes the longer-term vision.

Build state, 14 October 2026: S0 (skeleton) built and verified, tag `s0-skeleton`. Next: S1 (§11).

Security requirements must meet the president's demands.

The conversation owns the agent workspace under section 7. Generated code runs only in a task-lived sandbox through the authorized registry. Code execution and hosted controls are deferred in section 12.

| | Now | Deferred |
|---|---|---|
| Users | One team, local accounts | Organization accounts through Entra ID |
| Data | Simulated, labelled corpus | Source documents and engineering data |
| Language | English | French + language detection |
| Runs on | Laptops, Docker Compose | Azure, same images |
| Agent | One agent, run inside the CRAFT backend | Sub-agents, then specialists; optionally hosted by Foundry |
| Approvals | The requester decides effectful actions; the agent never decides | Designated approvers and refusal of self-approval under section 12 |

- Stack:
  - Python, FastAPI, msgspec, PostgreSQL with pgvector, SQLAlchemy
  - Microsoft Agent Framework, models from Microsoft Foundry
  - the front end, built against the `api/` contract
- Protocols: HTTP with JSON for ordinary calls, AG-UI for the chat stream, and MCP for agent tool calls.
