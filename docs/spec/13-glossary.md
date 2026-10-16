# 13. Glossary

HTTP means Hypertext Transfer Protocol. Python shapes use msgspec; the protocol terms below define the other abbreviations.

| Term | Meaning |
|---|---|
| Operation / handler | A function declared with `@operation`. Serves HTTP, MCP and Python with the same checks. Named `<feature>.<function>`; its tool name is `<function>` |
| Shape | A msgspec struct: an operation input or output |
| Approval gate | An effectful agent call needs an approved, unexecuted, matching request |
| MCP | Model Context Protocol: how agents list and call tools. In CRAFT, the adapter in section 3.3 offers every tool to the agent and to local MCP clients |
| Document provider | The folder that holds original files and their versions (section 8.3) |
| Library | Chunks, summaries, facet texts and vectors derived from the provider; rebuildable (section 8.4) |
| Chunk / citation handle | A block of a document version, the unit of search / item, version, block, locator and quote hash (section 8.4) |
| Facet | One way of describing a mission: purpose, design, operations, program, cost and risk (section 8.7) |
| Cube | Missions, silos, stages, the parameter dictionary and approved values (section 8.8) |
| Silo / stage | A kind of mission context / a mission lifecycle stage; each mission points at its current stage (section 8.8) |
| AG-UI | Agent-User Interaction protocol: typed events streaming an agent run to the browser |
| SSE | Server-Sent Events: a one-way HTTP stream, server to browser |
| Run / run token | One agent turn, possibly paused / its short-lived credential for `/mcp` |
| Unit of work | A short transaction selected by the service; all records commit or all roll back |
| Hybrid search / fusion | Normalized semantic and lexical scores, weighted 0.7/0.3, with permission trimming and deduplication |
| Foundry | Microsoft Foundry: Azure's platform for models and agents |
| Protected B | A classification for sensitive information |
| Slice | One step of the build order (§11), ending in a demo and a tag |
