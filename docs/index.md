# CRAFT

CRAFT answers questions about project documents with cited sources and maintains an engineering datacube. Its authenticated interface provides chat beside a personal JupyterLab workspace, a tools reference and saved skills. Conversations retain answers, evidence, approvals and uploaded or generated files.

This site contains CRAFT documentation. Run the application separately to use its authenticated APIs.

## Start here

- [Development](wiki/development.md): install, configure and check the repository.
- [Components](wiki/components.md): responsibilities and code locations.
- [Agent runtime](wiki/runtime.md): application runs, saved skills and decisions.
- [Process lifecycle](wiki/processes.md): owned services, readiness and shutdown.
- [Workspace](wiki/workspace.md): shared files, notebooks, source copies and native Python tools.
- [Identity and workspace ownership](wiki/identity.md): stable users, credentials, runtime boundaries and migration gates.
- [Identity table reference](wiki/identity-tables.md): current tables and relationship graphs.
- [Python workspace prototype](wiki/python.md): standalone code, CSV data and artifacts.
- [Documents and evidence](wiki/documents.md): identity, versions, extraction and relationships.
- [Model jobs](wiki/jobs.md): a standalone prototype for queued model work.

## Concepts

- [Scope](spec/01-scope.md)
- [Connections](spec/02-connections.md)
- [Agent](spec/03-agent.md)
- [Models and confidence](spec/04-models-and-confidence.md)
- [Operations and logging](spec/05-operations-and-logging.md)
- [Data](spec/07-data.md)
- [Engineering datacube](spec/08-engineering-datacube.md)
- [Accounts and access](spec/09-accounts-and-access.md)
- [Retrieval](spec/10-retrieval.md)

## Reference

Use the [API contract](API-Contract.md) for executable forms and the [dependency reference](Dependencies-and-Integration.md) for pins and integration. [Code layout](spec/06-code-layout.md) describes the application boundaries. [Glossary](spec/13-glossary.md), [traceability](spec/15-traceability.md), [build order](spec/11-build-order.md), [deferred register](spec/12-deferred-register.md) and [vision](spec/14-vision.md) retain their own topics.

[Document downloads](downloads.md) include the specification and section PDFs. Each topic has a Markdown download and an **Edit this page** link to its source on GitHub. Editing requires a GitHub account and follows the repository's change process.
