# 11. Build order

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP) and JavaScript Object Notation (JSON). Agent-User Interaction (AG-UI) carries the chat stream. Continuous integration (CI) checks the build.

S0 is implemented at tag `s0-skeleton`: locked dependencies, database initialization, a non-root image, strict settings, one pool, a transaction helper, error mapping, request identifiers, JSON logs and health. The steps below add application features in order. The front end builds against `api/examples/` in parallel.

Migration 0008 belongs to the early tables. The datacube uses migrations 0009 through 0011.

S1 ends at step 6, tagged `s1-documents`; S2 at step 8, tagged `s2-accounts`; S3 at step 21, tagged `s3-agent`; S4 at step 26, tagged `s4-approvals`; and S5 at step 32, tagged `s5-answers`.

1. Tables: accounts and call log (0001-0002)
2. Tables: conversations, runs and approvals (0003-0005)
3. Tables: library, drafts and workspace files (0006-0008)
4. @operation and @observe
5. MCP adapter for every tool
6. Document provider, library index and search, tag s1-documents
7. Accounts and sessions
8. /api/me and /dev, tag s2-accounts
9. Cube tables (0009-0011) and mission access
10. Office formats: Word, PDF, PowerPoint and spreadsheets
11. Demo seeder with ground truth
12. Parametric search, comparison and value evidence
13. Model adapter
14. Use case 1: /summarize page (pick or upload a document)
15. Version differences and citation checks
16. Candidate values from tables
17. Facet texts and similar missions
18. Agent runtime and chat stream, fake model
19. API Contract matches what is built
20. /mcp and run token
21. Conversations, answers and workspace files, tag s3-agent
22. Agent and scratchpad on the MCP, in a container
23. Chart and calculation-check tools
24. Approval gate
25. Drafts, saved conversations and export
26. Cube values through the gate, tag s4-approvals
27. Saved cube views in the workspace
28. Demo users, demo data and frozen snapshots
29. OpenText document provider, seeded with demo files
30. Bilingual file names in OpenText, through the approval gate
31. Evaluation set, vector search and fusion
32. Real answers, tag s5-answers

## 11.1 Milestone demonstrations

Each milestone ends with a demonstration from a fresh clone and its tag.

| Slice | Demonstration |
|---|---|
| S0 Skeleton | Start Compose and receive HTTP 200 from readiness. Stop the database and receive HTTP 503 within three seconds while liveness returns 200. CI passes. |
| S1 Data and operations | Seed and index, then search with `craft call library.search --as demo` and receive cited hits with one operation log row. The same search runs as the `search` tool through `craft mcp --as demo`. The unsigned HTTP route returns 401. |
| S2 Sign-in | Sign in and search on `/dev`. Sign out; the next signed-in request returns 401. |
| S3 Chat with the fake model | Chat streams text, a tool step and a citation. Log rows link the run, tool call and operation. |
| S4 Approvals | Save a draft and pause for approval. Restart the server before the requester approves. The draft executes once and appears in `GET /api/drafts`. |
| S5 Real answers | Produce cited answers with confidence over the simulated documents in the front end. |
