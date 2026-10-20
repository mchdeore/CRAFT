# 15. Traceability

Requirement IDs map to the implemented behavior in this specification.

| Requirements | Section | Implemented behavior |
|---|---|---|
| UMR-001, UMR-095 | 3.1, 3.7 | Free-text questions and one primary agent per conversation |
| UMR-002 | 8.4, 10 | Stable source identity and citation handles |
| UMR-003 | 4, 10 | Up to eight ranked authorized passages |
| UMR-009 | 3.5, 10 | Agent-selected search and exploration within the run budget |
| UMR-010, UMR-040, UMR-048 | 4.2 | Cited findings with zero, low confidence and explicit insufficiency |
| UMR-011, UMR-015, UMR-016, UMR-018 | 3.1, 3.7 | One agent, tool events and saved answer/retrieval activities |
| UMR-012, UMR-017 | 3.3, 5 | Registered tools and server-side permissions |
| UMR-013, UMR-019, UMR-030 | 3.5 | Tool, token, time and concurrent-run limits |
| UMR-014 | 7, 9 | Persisted conversation history and runtime state |
| UMR-020, UMR-068 | 3.5 | Retrieved content treated as data, tool allowlists and effect approvals |
| UMR-087, HITL-001 | 3.4 | Effectful agent requests pause for requester approval |
| UMR-023, HITL-004 | 3.4, 9 | The requester approves or rejects an owned frozen request |
| UMR-024, HITL-005 | 3.4 | Rejection returns a refused call to the agent |
| HITL-007 | 5.5, 7 | Append-only approval requests, decisions and execution events |
| UMR-028, UMR-081 | 4.1 | Selected models and versioned instructions |
| UMR-029, UMR-082, UMR-088 | 5, 7 | Declared effects and append-only history |
| UMR-035, UMR-039 | 3.4 | Approval mechanism for registered effectful tools |
| UMR-065 | 9 | Local accounts and two roles |
| PARAM-02 to 06, 39, 43, 44 | 3.5, 4.2, 9, 10 | Retrieval limits, confidence form and session lifetime |

## 15.1 Datacube capabilities

| Requirements | Section | Implemented behavior |
|---|---|---|
| UMR-032, UMR-033 | 8.13 | Similar missions with selected facets and a bounded result count |
| UMR-077 | 8.12 | Mission comparisons without design changes |
| UMR-006 | 8.4, 8.10, 8.12 | Value evidence and row citations |
| UMR-004, UMR-005, UMR-094 | 8.11, 10 | Scoped document retrieval |
| UMR-051, UMR-067 | 8.2, 8.3 | Local provider originals, immutable versions and derived library data |
| UMR-021, HITL-002 | 3.4, 8.14 | Cube publication after approval |
| UMR-022, HITL-003 | 8.14 | Frozen candidate evidence and reasoning |
| UMR-026, UMR-027 | 7, 8.10 | Append-only decisions and value history |
| UMR-066 | 8.15 | Mission and silo access |
| UMR-091 | 8.1, 8.17 | Labeled generated data |
| UMR-007, UMR-008, UMR-086 | 4, 8.12 | Evidence and explicit insufficiency |
| UMR-055 | 8.16 | Typed cube routes and tools |
| UMR-063 | 8.9 | Registered units and conversions |
| UMR-049, UMR-050 | 8.6, 8.8 | Block differences and current mission stage |
