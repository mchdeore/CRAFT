# 15. Traceability

Requirement IDs, mapped to the sections of this spec that cover them at the current stage.

| Requirements | § | Stage |
|---|---|---|
| UMR-001, UMR-095: free-text questions; a session with one primary agent | 3.1, 3.7 | Now (English) |
| UMR-075: answer in the question's language | 1 | Deferred |
| UMR-002: source identity | 10 | Now |
| UMR-003: enough evidence in context | 4.1, 10 | Now (top 8 passages) |
| UMR-009: search again; agent-selected retrieval tools | 10 | Now: 2 retries, one tool; Deferred: more tools |
| UMR-010, UMR-040, UMR-048: grounded answers, measured | 4.2, 10 | Now: groundedness check, Deferred: pass score on the acceptance set |
| UMR-064, UMR-080: acceptance set; quality monitoring | 4.1, 4.2, 10 | Deferred |
| UMR-011, UMR-015, UMR-016, UMR-018: one accountable agent; delegation; agent messages; plans | 3.1, 3.8 | Now: one agent, Deferred: delegation |
| UMR-012, UMR-017: tools only from the registry, within permissions | 3.3, 5 | Now |
| UMR-013, UMR-019, UMR-030: budgets, repetition stop, rate limits | 3.5 | Now: per-run limits; Deferred: repetition detection, cost budgets |
| UMR-014: episodic, semantic, procedural memory | 7 | Now: episodic, Deferred: the others |
| UMR-020, UMR-068: content policy; retrieved text untrusted | 3.5 | Now: data not instructions, allowlists, approvals; Deferred: measured filter rates |
| UMR-087, HITL-001: approval before high-risk or irreversible actions | 3.4 | Now: every effectful operation pauses |
| UMR-023, HITL-004: Approver role approves, rejects or edits | 3.4, 9 | Now: the requester approves or rejects; Deferred: Approver role, edits |
| UMR-024, HITL-005: retry after rejection | 3.4 | Now: rejection returns to the agent, Deferred: retry limit |
| UMR-025, HITL-006, HITL-008: timeouts, backup approver | 12 | Deferred |
| HITL-007: decisions logged; append-only actions and decisions | 5.5, 7 | Now |
| UMR-028, UMR-081: models and prompts versioned; model replaceable | 4.1 | Now |
| UMR-029, UMR-082, UMR-088: action classes; reversible changes | 5, 7 | Now: `effects` per operation, append-only drafts; Deferred: the full risk scheme |
| UMR-035, UMR-039: approval before cost or risk-register commitments | 3.4 | Now: the mechanism, Vision: those features |
| UMR-056, UMR-057, UMR-059, UMR-085: response time, availability, load | 12 | Deferred |
| UMR-058, UMR-069, UMR-070, UMR-073, UMR-074: classification, Protected B with Medium Integrity and Medium Availability (PBMM), encryption, Canadian regions, retention, backup | 12 | Deferred |
| UMR-065: Azure identity, role-based access | 9 | Now: local accounts, 2 roles; Deferred: Entra ID, 8 roles |
| UMR-072, UMR-079, UMR-084, UMR-093: personal information, notifications, guidance, code execution | 12 | Deferred |
| UMR-060, UMR-071, UMR-076 |  | Withdrawn |
| PARAM-02 to 06, 39, 43, 44: context, retrieval, retries, confidence gate, chunking, iterations, session lifetime | 3.5, 4.2, 9, 10 | Now: starting values in settings |
| PARAM-45 and the other PARAM entries | 3.8, 12 | Deferred, with their feature |

## 15.1 Datacube capabilities

| Requirements and capability | Section | Stage |
|---|---|---|
| UMR-031, UMR-034: cost estimates and uncertainty | 8.21, 12 | Deferred R4 |
| UMR-032: normalized parametric similarity | 8.10 | Now |
| UMR-033: closest missions with configurable count and cutoff | 8.10 | Now |
| UMR-053, UMR-083: authoritative financial evidence | 8.21, 12 | Deferred R4, read-only |
| UMR-077: option and planning comparisons without mission design changes | 8.7, 8.9, 8.13 | Now |
| UMR-006: cell evidence and deep citations | 8.6, 8.11 | Now |
| UMR-004, UMR-005, UMR-094: scoped document retrieval | 8.11, 10 | Now |
| UMR-051, UMR-067: original identity and reference boundary | 8.11, 8.16 | Now |
| UMR-021, HITL-002: cube changes approved before execution | 3.4, 8.17 | Now |
| UMR-022, HITL-003: exact candidate evidence and reasoning | 8.16, 8.17 | Now |
| UMR-026, UMR-027: append-only decisions and value history | 7, 8.18 | Now |
| UMR-066: mission, silo and evidence access | 8.17 | Now |
| UMR-091: simulated labels and isolated similarity sets | 8.6, 8.10 | Now |
| UMR-007, UMR-008, UMR-086: evidence-based confidence and insufficiency | 4, 8.6, 8.15 | Now |
| UMR-055: typed cube routes and tools | 8.13, 8.14 | Now |
| UMR-092: bounded approved workspace view | 7, 8.12, 8.13 | Now |
| UMR-049, UMR-050: stage history and styled differences | 8.21, 12 | Deferred R3 |
| UMR-046, UMR-061, UMR-062: reports and audit export | 8.21, 12 | Deferred R5 |
| UMR-063: registered units and versioned similarity settings | 8.5, 8.10 | Now |
| UMR-054: planning-tool static exports | 8.16, 12 | Deferred R2 |
| UMR-036, UMR-041, UMR-089: risk and anomaly operations | 8.15, 12 | Deferred |
| UMR-037, UMR-038: other risk identification | 12 | Deferred |
| UMR-042 to UMR-045, UMR-078: other anomaly and telemetry functions | 12 | Deferred |
| UMR-047, UMR-090: report review and background processing | 12, 14 | Deferred |
| UMR-052: source connectors | 12 | Deferred |
