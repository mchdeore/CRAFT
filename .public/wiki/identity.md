# Identity and workspace ownership

CRAFT uses a stable application user to own conversations, credentials and personal workspaces. Authentication proves that user; a workspace retains their files; a runtime executes their code. These have different lifecycles.

**Decision IAM-01 · 20 October 2026:** keep the existing account and operation systems and make workspace ownership explicit. A database constraint preserves a relationship; the operation and service still authorize each request. The [table reference](#table-reference) lists what is stored.

## The pattern CRAFT follows

There is no standard SQL schema covering users, passwords, workspaces, tools and MCP. OpenID Connect defines login identity, OAuth delegated access, SCIM provisioning and MCP tool communication. The common pattern in mature systems is a stable user with separate credentials, sessions and owned resources: Django has users, roles and sessions; Ory separates identity traits, credentials and sessions; JupyterHub keeps a user independently of their running server.

CRAFT uses that pattern:

- The CRAFT user ID owns conversations, runs, approvals, tokens and the workspace.
- craft-id, the identity provider CRAFT runs, proves who signs in through OpenID Connect. CRAFT links a person by the token's tenant and object ID (`tid`, `oid`), never by email or username ([access](access.md)).
- JupyterHub keeps each person's Hub user and starts and stops their server; the Hub user's name is the CRAFT user ID.

Passwords live only in craft-id, hashed with `argon2-cffi`. SHA-256 digests are used only for high-entropy random tokens, never for passwords.

## Current boundaries

The account, conversation, agent and approval tables use user foreign keys. Browser sessions store digests of random tokens with idle expiry, absolute expiry and revocation. Workspace tokens are account-scoped HTTP read capabilities. Agent runs have separate short-lived MCP credentials.

Personal workspace storage is outside these SQL tables. JupyterHub names each person's Hub user and server after their CRAFT user ID, and their files live on one volume, `claim-<user ID>`, that outlives the server. CRAFT reaches the Hub only through its own operations and one service token, and resolves the workspace from the trusted caller, never from a request. CRAFT keeps no table of workspaces, servers or volumes: the Hub owns that state ([workspace](workspace.md#workspaces-through-jupyterhub)).

`workspace_files` means **conversation attachments in PostgreSQL**. It does not contain the personal scripts, notebooks, Markdown skills or outputs under `USER_ROOT`. The database also keeps a separate saved-skill/revision API, notes and a draft model; personal Markdown files are the workspace skill source.

![Current account and conversation foreign keys](images/identity-current.svg)

The diagram shows selected foreign keys. A user can have no credential or conversation. Independent foreign keys do not enforce matching ownership between a run, conversation, message and approval; the services check it. `skill_revisions` is the exception: a composite foreign key to `skills` keeps the owner pair. `call_log` identifiers are correlation metadata without foreign keys.

## Workspace files

`.py`, `.ipynb`, `skills/*.md`, imports and outputs live on the person's volume beneath `USER_ROOT`. A file's identity is its owner and relative path. CRAFT keeps no catalog or revision copy of these files; personal copies of source documents stay outside source ingestion and embeddings, and source documents stay immutable and grant-scoped.

## Sessions and credentials

| Record or connection | Purpose | Ending behavior |
|---|---|---|
| `auth_sessions` | Browser sign-in | Sign-out ends the current session; password reset or disable revokes all of them; idle and absolute expiry |
| `workspace_tokens` | Account-scoped HTTP reads from personal Python | Own revoke or expiry; a disabled account is refused on each request. Sign-out and password reset do not revoke them |
| `agent_runs` credential | An active run's calls to CRAFT's MCP | Expiry, pause or run end; account and conversation checked per call. The browser session that started the run is provenance, not a validity check |
| `agent_sessions` | Saved agent state for a conversation | Replaced as runs save state; not a credential |
| JupyterHub sign-in and the server's Hub token | Direct editing and execution in JupyterLab | JupyterHub's own cookie, from a craft-id sign-in; CRAFT sign-out does not sign out of JupyterLab. The server's Hub token ends when the server stops |
| CRAFT's Hub service token | Start, stop and read servers; create a person's Hub user and link it to their craft-id account | Never expires; rotated by hand; never logged |

Each lifecycle change names which credentials it ends. Disabling an account refuses fresh CRAFT requests; it does not stop a kernel that is already running or end a JupyterLab session. Outbound credentials for outside services, when there are any, are stored as protected secret references, never as digests or plain settings, and CRAFT never passes its browser or run credential to an outside MCP server.

## Tools, MCP and code

The operation registry is the one definition of tool names, argument shapes, access and effects. HTTP, CRAFT's MCP and the Python package reuse it for authorization, validation, errors and metadata logging. User input and tool annotations cannot supply the trusted user or grant access. Personal skills contain instructions and references to workspace files; MCP keeps no copy of them.

CRAFT's MCP accepts only its own run credential ([tools over MCP](agent.md#tools-over-mcp)). It does not implement the federated OAuth flow of the MCP authorization specification.

## Runtime and execution

One workspace image and package serve chat and direct Python. Durable storage is separate from pods and kernels: the volume outlives the server, and the Hub starts and stops the server. Agent-run creation is serialized with a PostgreSQL transaction advisory lock. In one process, a second run for the same owner is refused and an existing kernel binding is never overwritten; this guard is not a lease across processes.

An approved external action records `executed` when its approval is consumed, before the call is dispatched, so it records consumption, not success. A crash or a lost response leaves the outcome unknown, and CRAFT never retries code or file writes after an ambiguous timeout.

## Maintaining the decision

A change to these invariants updates this page and the table reference in the same pull request as its migration and tests, with the reason and its effect on credentials and ownership.

## Sources

Official sources accessed 20 October 2026. Protocol rules, framework examples and the CRAFT design above have different authority.

- [OpenID Connect Core 1.0, §5.7](https://openid.net/specs/openid-connect-core-1_0.html#ClaimStability): issuer/subject identity stability.
- [SCIM Core Schema, RFC 7643, §§3.1–4](https://www.rfc-editor.org/rfc/rfc7643): provisioning IDs and User/Group resources.
- [OAuth security BCP, RFC 9700, §§2.4, 4.14](https://www.rfc-editor.org/rfc/rfc9700): password-grant prohibition and refresh-token protections.
- [MCP authorization, 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization): protected resources, discovery, audience and credential boundaries.
- [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) and [session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html): maintained password hashing and server-enforced session lifecycle.
- [Django 6.0 authentication](https://docs.djangoproject.com/en/6.0/ref/contrib/auth/) and [sessions](https://docs.djangoproject.com/en/6.0/topics/http/sessions/): framework conventions, not a workspace schema.
- [Ory identities](https://www.ory.com/docs/kratos/manage-identities/overview) and [sessions](https://www.ory.com/docs/kratos/session-management/overview): identity/credential/session separation.
- [JupyterHub User](https://jupyterhub.readthedocs.io/en/stable/reference/api/user.html) and [Spawner](https://jupyterhub.readthedocs.io/en/stable/reference/api/spawner.html): persistent users and managed runtime state.
- [PostgreSQL 18 constraints](https://www.postgresql.org/docs/18/ddl-constraints.html) and [row security](https://www.postgresql.org/docs/18/ddl-rowsecurity.html): composite ownership FKs, null behavior, indexes and policy limitations.

Implementation evidence: [accounts](https://github.com/mchdeore/CRAFT/blob/main/app/accounts/storage.py), [identity resolution](https://github.com/mchdeore/CRAFT/blob/main/app/infrastructure/access.py), [workspace selection](https://github.com/mchdeore/CRAFT/blob/main/app/infrastructure/workspaces.py), [agent storage](https://github.com/mchdeore/CRAFT/blob/main/app/agent/storage.py), [approval execution](https://github.com/mchdeore/CRAFT/blob/main/app/approvals/service.py), [operation registry](https://github.com/mchdeore/CRAFT/blob/main/app/infrastructure/operations/registry.py) and [migration history](https://github.com/mchdeore/CRAFT/tree/main/app/infrastructure/database/migrations/versions).

## Table reference

These are the tables the numbered migrations create, apart from Alembic's own `alembic_version`. This reference describes the declared schema, not a live database.

### Accounts, conversations and personal ownership

| Table | Key and relationship | Responsibility |
|---|---|---|
| `users` | UUID PK; unique lowercase username | Durable local identity, display name and disable state |
| `local_credentials` | User PK/FK | At most one local password hash per user |
| `user_roles` | `(user_id, role)` PK; user FK | Current `member`/`admin` grants |
| `auth_sessions` | UUID PK; user FK; unique token digest | Expiring, revocable browser login |
| `workspace_tokens` | UUID PK; user FK; unique 32-byte token digest | Account-scoped HTTP read credentials |
| `access_requests` | Tenant/object ID PK | A person who signed in at craft-id but has no CRAFT account yet, waiting for `craft users approve` |
| `sign_in_flows` | Digest PK | A sign-in in progress between the redirect to craft-id and the callback |
| `mission_grants` | UUID PK; user FK; unique user/mission/silo | Source access; mission/silo strings do not have catalog FKs |
| `conversations` | UUID PK; owner FK | Chat title, timestamps and archive state |
| `messages` | UUID PK; conversation FK; optional run FK; unique conversation/sequence | Append-only history |
| `agent_runs` | UUID PK; conversation and starter FKs | Run status and temporary MCP credential |
| `agent_sessions` | Conversation PK/FK | Saved agent framework state |
| `agent_run_inputs` | Run/segment PK; owner FK | What a start or resume gives the agent worker |
| `agent_run_events` | Run/segment/sequence PK | A run's stream events, which a browser re-attaches to |
| `approval_requests` | UUID PK; run, conversation and requester FKs | Frozen operation, arguments and digest |
| `approval_events` | UUID PK; request FK; optional actor FK | Append-only decisions and execution consumption |
| `notebook_grants` | UUID PK; user, conversation and unique approval FKs | A requester's grant to run or edit one notebook in one conversation without asking again |
| `call_log` | Numeric PK; correlation IDs without FKs | Operation metadata and outcome, including workspace-token ID |
| `workspace_files` | UUID PK; conversation FK; optional export-approval FK | SQL chat attachments and generated charts; not Jupyter personal files |
| `notes` | UUID PK; owner FK; optional conversation FK | Existing owner-scoped note API |
| `drafts` | UUID PK; owner, optional parent/approval FKs | Declared draft storage |
| `skills` | UUID PK; owner FK; unique ID/owner | Existing database saved-skill API |
| `skill_revisions` | Skill/revision PK; composite skill/owner FK; unique owner/mutation ID | Owned immutable saved-skill revisions |
| `document_revisions` | UUID PK; author FK; unique item/revision | CRAFT's own immutable revisions of a source document |

The current [account diagram](images/identity-current.svg) shows the core FKs. The database enforces the skill/revision owner pair. Other independent owner/conversation/run FKs require service checks to prevent scope mismatch. A primary key identifies a row; it grants no request permission.

### Source library and datacube

Personal files and their skills are not stored or embedded in these source tables.

| Table | Key and relationship | Responsibility |
|---|---|---|
| `provider_items` | Source item PK; mission/silo/stage strings without FKs | Source catalog and withdrawal state |
| `doc_versions` | UUID PK; item FK; unique item/version | Indexed source version, hash and summary |
| `chunks` | UUID PK; cascading version FK; unique version/block | Derived searchable source blocks |
| `vectors` | UUID PK; polymorphic owner kind/ID without FK | Derived chunk/version/facet embeddings |
| `version_diffs` | From/to/sequence PK; cascading version FKs | Derived version comparisons |
| `index_cursor` | Provider PK | Source indexing progress |
| `silos` | Vocabulary key PK | Controlled source domain |
| `stages` | Vocabulary key PK | Controlled source lifecycle stage |
| `missions` | Mission key PK; current-stage FK | Source mission |
| `mission_aliases` | Alias PK; mission FK | Mission lookup names |
| `elements` | UUID PK; mission, silo and optional parent FKs | Mission/silo-specific element |
| `parameters` | Parameter key PK | Quantity, units and validity range |
| `unit_conversions` | Parameter/unit PK; parameter FK | Quantity unit conversion |
| `header_synonyms` | Header/parameter PK; parameter FK | Table-header recognition |
| `cube_values` | UUID PK; mission/silo/stage/element/parameter/approval FKs | Accepted observations with source provenance |
| `facet_texts` | UUID PK; mission FK; unique mission/facet | Derived mission-facet text and source references |

![Current source foreign keys](images/identity-source.svg)

Arrows name actual FK columns. `vectors` owner references, `facet_texts` source arrays and cube item/version/block provenance are not FK-enforced. Their consistency and deletion need explicit source-index handling; the diagram does not imply authorization or semantic accuracy.

### craft-id

Only craft-id's own `craft_id` login is granted these tables; CRAFT's application login is not.

| Table | Key and relationship | Responsibility |
|---|---|---|
| `id_accounts` | UUID PK; unique username | The people who can sign in |
| `id_credentials` | Account PK/FK | Their Argon2id password hashes |
| `id_clients` | UUID PK | The registered OpenID Connect clients, such as CRAFT and JupyterHub, with a digest of each secret and their redirect addresses |
| `id_codes` | Code digest PK; account and client FKs | Short-lived authorization codes |

### Background jobs

| Table | Key and relationship | Responsibility |
|---|---|---|
| `procrastinate_jobs`, `procrastinate_events`, `procrastinate_workers`, `procrastinate_periodic_defers` | Procrastinate's own schema | The job queue ([background jobs](processes.md#background-jobs)) |
| `job_attempt_errors` | Job/attempt PK | The error class of each failed attempt |
| `queue_samples` | Numeric PK | Queue depth sampled every 30 seconds |

### Outside the current SQL schema

| Component | Where it lives |
|---|---|
| Personal workspace | A JupyterHub user named by the CRAFT user ID, with one volume, `claim-<user ID>` |
| Scripts, notebooks, Markdown skills and outputs | The owner's volume beneath `USER_ROOT`, through the Jupyter Contents API |
| Server, kernel and native bridge binding | JupyterHub, and the agent worker's in-process run state |
| Executable tools | The operation registry in code |
| External login | `users.idp_tenant_id` and `users.idp_object_id`, unique together, set at the first craft-id sign-in |



Schema sources: [shared metadata](https://github.com/mchdeore/CRAFT/blob/main/app/infrastructure/database/metadata.py), [accounts](https://github.com/mchdeore/CRAFT/blob/main/app/accounts/storage.py), [conversations](https://github.com/mchdeore/CRAFT/blob/main/app/conversations/storage.py), [agent](https://github.com/mchdeore/CRAFT/blob/main/app/agent/storage.py), [approvals](https://github.com/mchdeore/CRAFT/blob/main/app/approvals/storage.py), [audit](https://github.com/mchdeore/CRAFT/blob/main/app/infrastructure/operations/storage.py) and [migrations](https://github.com/mchdeore/CRAFT/tree/main/app/infrastructure/database/migrations/versions).
