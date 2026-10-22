# Glossary

HTTP means Hypertext Transfer Protocol. Python shapes use msgspec; the protocol terms below define the other abbreviations.

| Term | Meaning |
|---|---|
| Operation / handler | A function declared with `@operation`. Serves HTTP, MCP and Python with the same checks. Named `<feature>.<function>`; its tool name is `<function>` |
| Shape | A msgspec struct: an operation input or output |
| Approval gate | An effectful agent call needs an approved, unexecuted, matching request |
| MCP | Model Context Protocol: how agents list and call tools. In CRAFT, the adapter in [Tools over MCP](agent.md#tools-over-mcp) offers every tool to the agent and to local MCP clients |
| Document provider | The folder that holds original files and their versions ([Document provider](datacube.md#document-provider)) |
| Library | Chunks, summaries, facet texts and vectors derived from the provider; rebuildable ([Library: chunks, summaries and citations](datacube.md#library-chunks-summaries-and-citations)) |
| Chunk / citation handle | A block of a document version, the unit of search / item, version, block, locator and quote hash ([Library: chunks, summaries and citations](datacube.md#library-chunks-summaries-and-citations)) |
| Facet | One way of describing a mission: purpose, design, operations, program, cost and risk ([Facets](datacube.md#facets)) |
| Cube | Missions, silos, stages, the parameter dictionary and approved values ([Missions, silos and stages](datacube.md#missions-silos-and-stages)) |
| Silo / stage | A kind of mission context / a mission lifecycle stage; each mission points at its current stage ([Missions, silos and stages](datacube.md#missions-silos-and-stages)) |
| AG-UI | Agent-User Interaction protocol: typed events streaming an agent run to the browser |
| SSE | Server-Sent Events: a one-way HTTP stream, server to browser |
| Run / run token | One agent turn, possibly paused / its short-lived credential for `/mcp` |
| Unit of work | A short transaction selected by the service; all records commit or all roll back |
| Hybrid search / fusion | Normalized semantic and lexical scores, weighted 0.7/0.3, with permission trimming and deduplication |
| Foundry | Microsoft Foundry: Azure's platform for models and agents |
| Protected B | A classification for sensitive information |
| Slice | One step of the build order ([Now](roadmap.md#now)), ending in a demo and a tag |
