# 13. Glossary

HTTP means Hypertext Transfer Protocol. Python shapes use msgspec; the protocol terms below define the other abbreviations.

| Term | Meaning |
|---|---|
| Operation / handler | A function declared with `@operation`. Serves HTTP, MCP and Python with the same checks. Named `<feature>.<function>`; its tool name is `<function>` |
| Shape | A msgspec struct: an operation input or output |
| Approval gate | An effectful agent call needs an approved, unexecuted, matching request |
| MCP | Model Context Protocol: how agents list and call tools. In CRAFT, agent to services only |
| AG-UI | Agent-User Interaction protocol: typed events streaming an agent run to the browser |
| SSE | Server-Sent Events: a one-way HTTP stream, server to browser |
| Run / run token | One agent turn, possibly paused / its short-lived credential for `/mcp` |
| Unit of work | A short transaction selected by the service; all records commit or all roll back |
| Hybrid search / fusion | Normalized semantic and lexical scores, weighted 0.7/0.3, with permission trimming and deduplication |
| Foundry | Microsoft Foundry: Azure's platform for models and agents |
| Protected B | A classification for sensitive information |
| Slice | One step of the build order (§11), ending in a demo and a tag |
