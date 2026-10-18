# 7. Data

Structured Query Language (SQL) defines the database tables. Identifiers are universally unique identifiers (UUIDs). Token and source digests use the 256-bit Secure Hash Algorithm (SHA-256). The application programming interface (API) uses Hypertext Transfer Protocol (HTTP). Agent-User Interaction (AG-UI) describes stored messages. The command-line interface (CLI) uses application operations.

- All state in PostgreSQL:
  - SQLAlchemy tables and queries, Psycopg driver, Alembic migrations, pgvector for vectors;
  - sync database code, with handlers in FastAPI's thread pool.
- Services select short database transactions, read-only for reads. Storage receives an explicit Session and never commits independently.
  - No transaction spans a model call, retrieval outside the database or approval wait. Cross-feature calls retain trusted context and resource permissions.
- Database logins: `postgres` (superuser) only runs `docker/initdb.sh`. Migrations run as `craft_owner`; the app connects as `craft_app`.
  - `craft_app` gets `SELECT` by default and can't create tables. Each migration grants writes per table.
  - Append-only tables grant `craft_app` only `INSERT`, `SELECT`.

## 7.1 Logins

| Login | Used by | Rights |
|---|---|---|
| `postgres` | `docker/initdb.sh`, once, on an empty volume | Superuser. Needed to create the vector extension |
| `craft_owner` | Migrations and the seed command | Owns `craft_dev` and `craft_test`; creates and changes tables |
| `craft_app` | The running application, tests, command line | Reads every table by default. Writes only where a migration grants it. Cannot create tables |

## 7.2 Migrations and tables

- Migrations:
  - files `migrations/versions/NNNN_<verb>_<thing>.py`, revision ID = `NNNN`;
  - one linear history, never edited after merge;
  - applied with `uv run alembic upgrade head`, never at startup.
- Defaults:
  - `id` = `uuid primary key default uuidv7()` (time-ordered, built into PostgreSQL);
  - `ts` = `timestamptz not null default now()`;
  - every column is `not null` unless it says `null`.

Tables and columns use the stated defaults. In the SQL, `id` is the UUID default above and `ts` is the timestamp default above. Each migration comment lists the application write grants. Account roles, conversation ownership and append-only records follow the rules in this section. Adding values to a database check constraint requires a migration. Partial unique indexes permit one decision and one execution per approval.

```sql
-- 0001_create_accounts                              craft_app: INSERT, UPDATE (user_roles: INSERT, DELETE)
create table users (id, username text not null unique check (username = lower(username)),
  display_name text not null, disabled_at timestamptz null, created_at ts);
create table local_credentials (user_id uuid primary key references users,
  password_hash text not null, updated_at ts);                                     -- Argon2id
create table user_roles (user_id uuid references users,
  role text check (role in ('member','admin')), granted_at ts, primary key (user_id, role));
create table auth_sessions (id, user_id uuid not null references users,
  token_sha256 bytea not null unique, created_at ts, last_seen_at ts,
  idle_expires_at timestamptz not null, expires_at timestamptz not null,
  revoked_at timestamptz null, revoked_reason text null);
create index on auth_sessions (user_id);

-- 0002_create_call_log                              craft_app: INSERT, SELECT
create table call_log (id bigint generated always as identity primary key, at ts,
  kind text check (kind in ('operation','step')), name text not null,
  origin text check (origin in ('http','mcp','python')), outcome text check (outcome in ('ok','error')),
  error_code text null, duration_ms int not null, db_statements int not null default 0,
  db_ms int not null default 0, request_id uuid not null, call_id uuid not null,
  parent_call_id uuid null, user_id uuid null, auth_session_id uuid null,
  conversation_id uuid null, run_id uuid null, tool_call_id text null);    -- no foreign keys, no content
create index on call_log (at); create index on call_log (user_id, at);
create index on call_log (run_id); create index on call_log (request_id);

-- 0003_create_conversations                         conversations: INSERT, UPDATE; messages: INSERT, SELECT
create table conversations (id, owner_id uuid not null references users, title text not null,
  created_at ts, updated_at ts, archived_at timestamptz null);
create index on conversations (owner_id, updated_at desc);
create table messages (id, conversation_id uuid not null references conversations, seq int not null,
  role text check (role in ('user','assistant','tool','activity')),
  content jsonb not null, run_id uuid null, created_at ts,                -- content: the AG-UI message
  unique (conversation_id, seq));

-- 0004_create_agent_runs                            craft_app: INSERT, UPDATE
create table agent_runs (id, conversation_id uuid not null references conversations,
  started_by uuid not null references users,
  status text check (status in ('running','paused','finished','failed','cancelled')),
  model text not null, instructions_version text not null,
  token_sha256 bytea null unique, token_expires_at timestamptz null,
  started_at ts, ended_at timestamptz null);
create table agent_sessions (conversation_id uuid primary key references conversations,
  state jsonb not null, updated_at ts);                                    -- AgentSession.to_dict()
alter table messages add foreign key (run_id) references agent_runs;

-- 0005_create_approvals                             craft_app: INSERT, SELECT only
create table approval_requests (id, run_id uuid not null references agent_runs,
  conversation_id uuid not null references conversations, requested_by uuid not null references users,
  operation text not null, arguments jsonb not null, arguments_sha256 bytea not null,
  evidence jsonb not null, reasoning text not null, retrieval_path jsonb not null,
  interrupt_id text not null unique, created_at ts);
create table approval_events (id, request_id uuid not null references approval_requests,
  kind text check (kind in ('requested','approved','rejected','executed','failed')),
  actor_id uuid null references users, at ts, note text null);              -- null actor = the system
create unique index on approval_events (request_id) where kind in ('approved','rejected');
create unique index on approval_events (request_id) where kind in ('executed','failed');
create view approval_status as select distinct on (request_id) request_id, kind, actor_id, at
  from approval_events order by request_id, at desc, id desc;

-- 0006_create_library                              craft_app: SELECT (indexing writes as craft_owner);
--                                                  mission_grants: INSERT, DELETE
create table provider_items (item text primary key check (item ~ '^itm_[0-9a-f]{12}$'),
  path text not null, title text not null, mission_key text null, silo text null, stage text null,
  simulated boolean not null,
  classification text check (classification in ('unclassified','protected-a','protected-b')),
  deleted_at timestamptz null, updated_at ts);                  -- tags come from the provider path
create table doc_versions (id, item text not null references provider_items, version int not null,
  sha256 bytea not null check (octet_length(sha256) = 32), media_type text not null,
  summary text not null, summary_kind text check (summary_kind in ('extract','generated')),
  summary_tsv tsvector generated always as (to_tsvector('english', summary)) stored,
  indexed_at ts, unique (item, version));
create index on doc_versions using gin (summary_tsv);
create table chunks (id, doc_version_id uuid not null references doc_versions on delete cascade,
  block int not null, kind text check (kind in ('text','table','slide','sheet')),
  heading_path text not null, locator text not null, text text not null,
  text_sha256 bytea not null check (octet_length(text_sha256) = 32),
  tsv tsvector generated always as (to_tsvector('english', heading_path || ' ' || text)) stored,
  unique (doc_version_id, block));
create index on chunks using gin (tsv);
create table vectors (id, owner_kind text check (owner_kind in ('chunk','doc_version','facet_text')),
  owner_id uuid not null, model text not null, recipe text not null,
  embedding vector(1536) not null, unique (owner_kind, owner_id));
create index on vectors using hnsw (embedding vector_cosine_ops);
create table version_diffs (from_version uuid references doc_versions on delete cascade,
  to_version uuid references doc_versions on delete cascade, seq int not null,
  change text check (change in ('added','removed','changed','moved')),
  from_block int null, to_block int null, primary key (from_version, to_version, seq));
create table index_cursor (provider text primary key, cursor bigint not null, updated_at ts);
create table mission_grants (id, user_id uuid not null references users, mission_key text not null,
  silo text null, granted_at ts, unique nulls not distinct (user_id, mission_key, silo));

-- 0007_create_user_documents                        craft_app: INSERT, SELECT only
create table drafts (id, owner_id uuid not null references users, title text not null,
  body text not null, based_on uuid null references drafts,
  approval_request_id uuid null references approval_requests, created_at ts);
create index on drafts (owner_id, created_at desc);                         -- an edit is a new row

-- 0008_create_workspace_files (s1)                 craft_app: INSERT, SELECT, UPDATE, DELETE
create table workspace_files (id, conversation_id uuid not null references conversations,
  name text not null, media_type text not null, size_bytes int not null, sha256 bytea not null,
  bytes bytea null, origin text check (origin in ('upload','agent')),
  state text check (state in ('uploading','processing','ready','failed','export-pending','exported')),
  simulated boolean not null,
  classification text check (classification in ('unclassified','protected-a','protected-b')),
  status_detail text null, export_approval_id uuid null references approval_requests, created_at ts);
create index on workspace_files (conversation_id, created_at desc);    -- deleted when the conversation is archived
```

## 7.3 Table rules

| Area | Rules |
|---|---|
| Accounts | Session token digests and password hashes only. Accounts by CLI. Roles = rows in `user_roles` |
| Call log | Holds linked account, sign-in session, conversation, run and tool-call identifiers without content. Approval records hold decisions separately. |
| Conversations | Owned by an account. Sign-out deletes nothing. History comes from `messages` as stream-protocol messages. A later run restores `agent_sessions.state` with `AgentSession.from_dict`. |
| Agent runs | Only the run-token digest is stored. A running run finishes, fails, cancels or pauses; a decision can resume a paused run. |
| Approvals | Every step a new row; current state = latest event; the database refuses edits and deletes |
| Library | Derived from the document provider and rebuilt by `craft index` (section 8.6). Never holds an original file. A chunk is the unit of search and citation; a citation names item, version, block, locator and quote hash. `simulated` and `classification` label every chunk and citation. Access comes from `mission_grants` (section 8.15) |
| Workspace | The files of one conversation, uploaded or made by the agent. A file is simulated only if all its inputs are, and takes the highest classification among them. Deleted when the conversation is archived; keeping one or sending it outside CRAFT is an export, which needs approval. Every download is in the call log |
| User documents | `drafts` belong to their owner. Append-only. Shared documents (Vision) build on this pattern |

Answer retrieval records live in `messages.content` as typed `craft.retrieval` AG-UI activities beside `craft.answer`. The activity holds messageId, runId, model, instructionsVersion and path. No new table is needed. File operations use the existing workspace table and declared application grants. Limits are 25 MiB per file and 50 files; conversation-row locks serialize counting and updates. Archive and run start share account serialization. Archive refuses active runs, removes workspace rows and retains history.

## 7.4 Seed and health

- Seed (not a migration): `uv run craft seed base` loads demo users, their mission grants and a small provider folder; `uv run craft seed demo` adds the demo set (section 8.17). `uv run craft index` then builds the library (fake embeddings in fake-model mode). Seeding and indexing run as `craft_owner`, like a migration, and are idempotent. Account creation and grants through `craft users` use the application login.
- Liveness returns HTTP 200 with an ok body while the process runs. Readiness returns HTTP 200 when the database reports the expected migration within three seconds; otherwise it returns HTTP 503 with an unavailable body. Its separate connection has a three-second deadline. Both routes are anonymous, outside `/api`, and reveal no protected information. Hosted probes use the same split.
- Section 12 defines database deployment, permission, backup, retention and audit extensions. Section 1 defines the workspace and sandbox scope.
