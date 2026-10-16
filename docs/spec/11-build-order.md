# 11. Build order

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP) and JavaScript Object Notation (JSON). Agent-User Interaction (AG-UI) carries the chat stream. Continuous integration (CI) checks the build.

S0 is implemented at tag `s0-skeleton`: locked dependencies, database initialization, a non-root image, strict settings, one pool, a transaction helper, error mapping, request identifiers, JSON logs and health. The steps below add application features in order. The front end builds against `api/examples/` in parallel.

Migration 0008 belongs to the early tables. The datacube uses migrations 0009 through 0011.

S1 ends at step 6, tagged `s1-documents`; S2 at step 8, tagged `s2-accounts`; S3 at step 20, tagged `s3-agent`; S4 at step 23, tagged `s4-approvals`; and S5 at step 26, tagged `s5-answers`.

1. Tables: accounts and call log (0001-0002)
2. Tables: conversations, runs and approvals (0003-0005)
3. Tables: documents, drafts and workspace files (0006-0008)
4. @operation and @observe
5. Documents: read, keyword search, preview and download
6. Seed and command line, tag s1-documents
7. Accounts and sessions
8. /api/me and /dev, tag s2-accounts
9. Datacube tables (0009-0011) and access filter
10. Ingest files: text to blocks
11. Metadata with evidence and content duplicates
12. Parameter dictionary and parametric search
13. Model adapter
14. Use case 1: /summarize page (pick or upload a document)
15. Candidate value intake
16. Mission comparison and value evidence
17. Similar missions and distance explanations
18. Agent runtime and chat stream, fake model
19. /mcp and run token
20. Conversations, answers and workspace files, tag s3-agent
21. Approval gate
22. Drafts, saved conversations and export
23. Datacube values and definitions through the gate, tag s4-approvals
24. Bounded datacube view in the workspace
25. Evaluation set, vector search and fusion
26. Real answers, tag s5-answers

## 11.1 Milestone demonstrations

Each milestone ends with a demonstration from a fresh clone and its tag.

| Slice | Demonstration |
|---|---|
| S0 Skeleton | Start Compose and receive HTTP 200 from readiness. Stop the database and receive HTTP 503 within three seconds while liveness returns 200. CI passes. |
| S1 Data and operations | Search with `craft call documents.search_documents --as demo` and receive hits with one operation log row. The unsigned HTTP route returns 401. |
| S2 Sign-in | Sign in and search on `/dev`. Sign out; the next signed-in request returns 401. |
| S3 Chat with the fake model | Chat streams text, a tool step and a citation. Log rows link the run, tool call and operation. |
| S4 Approvals | Save a draft and pause for approval. Restart the server before the requester approves. The draft executes once and appears in `GET /api/drafts`. |
| S5 Real answers | Produce cited answers with confidence over the simulated documents in the front end. |
