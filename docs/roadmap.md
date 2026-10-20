# Demo roadmap

## 1. Available foundation

CRAFT provides owned chat, saved skill revisions, document and cube retrieval, citations, chart and calculation tools, conversation files and requester approvals. The specification describes these built operations. The public site hosts documentation; the authenticated application runs separately.

## 2. Small demo sequence

1. Correct and build the documentation.
2. Pin the application, frontend, provider data and generated seed; start the selected services with an empty disposable database.
3. Exercise an engineering document search with cited evidence and a finance value journey with a small chart or calculation, using the existing APIs and a saved skill.
4. Verify an approved synthetic note executes once, a rejected note writes nothing, and reload, cancellation and replay preserve the recorded outcome.
5. Check ownership, ambiguous or missing evidence, skill save/reload, previews and downloads.
6. Integrate the latest frontend source after the core checks, repeat full acceptance and freeze the tested revision.

The Python runner, shared code broker, durable job queue, notebook editor, document graph and expanded chat organization are future work. They are separate from this demo sequence.

## 3. References

See [runtime](wiki/runtime.md), [documents and evidence](wiki/documents.md), [models and confidence](spec/04-models-and-confidence.md), [accounts and access](spec/09-accounts-and-access.md), [API contract](API-Contract.md) and [dependency reference](Dependencies-and-Integration.md).
