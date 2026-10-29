# Accounts and access

The command-line interface (CLI) creates local accounts. Agent-User Interaction (AG-UI) defines conversation messages. The application programming interface (API) uses Hypertext Transfer Protocol (HTTP). The 256-bit Secure Hash Algorithm (SHA-256) produces token digests. Structured Query Language (SQL) defines session rows.

## Accounts

| Item | Now | Deferred |
|---|---|---|
| Sign-in | People sign in at craft-id, the identity provider CRAFT runs, through the OpenID Connect code flow ([identity](identity.md)). craft-app receives a signed ID token and links the person to a CRAFT user by tenant and object ID. Someone without an account sees "no access yet" until an admin runs `craft users approve`. Passwords live only in craft-id (Argon2id, at least `CRAFT_PASSWORD_MIN_LENGTH` characters), and accounts are created with `craft id users add`. A sign-in that fails at the provider, including craft-id being unreachable or answering with something malformed, returns to the interface with `signIn=failed` instead of an error. No sign-up or email recovery | None: craft-id mirrors Entra ID's flow and claims and is permanent |
| Roles | `member` uses CRAFT; `admin` also manages accounts through `craft users promote`. Operation and resource permissions apply to both | Expanded role mapping. New values in a database check constraint (`CHECK`) require a migration |
| Approvals | The requester decides an effectful action under [Human approval](agent.md#human-approval). The agent cannot decide | Expanded decision policy in [Deferred](roadmap.md#deferred) |

Rehash passwords at sign-in when the `argon2-cffi` defaults change. Password sign-in at `POST /api/session` is off unless `CRAFT_LOCAL_PASSWORD_SIGNIN` is set, which only tests do.

## Cookie and session row

```text
__Host-craft_session=<token>; Secure; HttpOnly; SameSite=Lax; Path=/
```

```sql
create table auth_sessions (id uuid primary key default uuidv7(),
  user_id uuid not null references users, token_sha256 bytea not null unique,
  created_at timestamptz not null default now(), last_seen_at timestamptz not null default now(),
  idle_expires_at timestamptz not null, expires_at timestamptz not null,
  revoked_at timestamptz null, revoked_reason text null);
create index on auth_sessions (user_id);
```

Tokens are 32 random bytes, replaced at every sign-in and stored only as SHA-256 digests. The `__Host-` prefix binds the cookie to the host; `HttpOnly` keeps it away from page scripts. Tokens never appear in logs, uniform resource locators (URLs) or bodies. Sign-in tokens stay out of browser storage. [Migrations and tables](datacube.md#migrations-and-tables) defines the schema.

The front end forwards `/api` and `/health` to `http://localhost:8000` through its development server and uses the same origin. Cookie lookup runs in `app/infrastructure/access.py`. Handlers receive the trusted `Caller` and never read cookies.

## Flows

| Flow | Server steps | Result |
|---|---|---|
| Sign in: `POST /api/session` | Rate-limit the username. Load the account and verify its hash; use a dummy hash for a missing account. Refuse disabled accounts. Create a new token and session with an eight-hour idle expiry and twelve-hour absolute expiry. Set the cookie. | `200 Me`; wrong username or password both give `401`; rate limit gives `429` |
| Each request | Hash the cookie token. Find an unrevoked session whose expiries are ahead. Load the enabled account and its current roles into `Caller`. | Trusted `Caller`, otherwise `401` |
| Keep alive | At most once a minute, update last-seen time and extend idle expiry by eight hours, capped at the absolute expiry. | One short update |
| Sign out: `DELETE /api/session` | Revoke the sign-in session with reason `sign_out` and clear the cookie. | `204`. Sign-out deletes nothing. The workspace stays with its conversation until the conversation is archived under [Database](datacube.md#database) |
| Password reset or account disable | Revoke every open sign-in session for the account. | The next request gives `401` |
| Role change | Load the changed roles on the next request. | No session revocation is required |

`POST`, `PUT`, `PATCH` and `DELETE` require an `Origin` listed in `CRAFT_ALLOWED_ORIGINS`; otherwise they return HTTP 403. The default is `http://localhost:8000`. Add the front end's development origin. `SameSite=Lax` also constrains cross-site requests.

```python
# app/accounts/handlers.py
@operation(PUBLIC, post="/session")
def sign_in(caller: Caller, request: SignIn) -> Me: ...     # sets the cookie on the response

@operation(SIGNED_IN, delete="/session")
def sign_out(caller: Caller) -> None: ...

@operation(SIGNED_IN, get="/me")
def me(caller: Caller) -> Me: ...
```

Each request resolves its session, checks the operation permission, applies the agent effect gate, checks the service resource permission and filters readable passages before retrieval ranking.

## Settings

| Setting | Default |
|---|---|
| `CRAFT_SESSION_IDLE_HOURS` | 8 |
| `CRAFT_SESSION_ABSOLUTE_HOURS` | 12 |
| `CRAFT_SIGN_IN_ATTEMPTS_PER_MINUTE` | 5 |
| `CRAFT_PASSWORD_MIN_LENGTH` | 12 |
| `CRAFT_ALLOWED_ORIGINS` | `http://localhost:8000` |
| `CRAFT_MCP_URL` | `http://127.0.0.1:8000/mcp` (loopback only) |

Settings validate at startup. The keep-alive update extends idle expiry by eight hours, at most once a minute, and never beyond the twelve-hour absolute expiry.

## Conversation storage

| Table | Holds | Written |
|---|---|---|
| `conversations` | Owner, title, archived | Create, rename, archive |
| `messages` | Numbered, append-only AG-UI messages: user, assistant, tool, and a `craft.answer` activity with citations and confidence; run | Each user message; each agent message at the end of a run |
| `workspace_files` | The conversation's files, uploaded or made by the agent; deleted when the conversation is archived | Upload, agent output, delete, export |
| `agent_runs` | Status, model, instructions version, run token digest | At run start, pause and end |
| `agent_sessions` | The agent's saved state, one row per conversation | Before and after each run |

Every access checks the owner; a conversation identifier grants no permission. History comes only from `messages`. CRAFT stores conversation history and agent state under [Database](datacube.md#database). Model response storage follows [Models](agent.md#models); run tokens follow [Tools over MCP](agent.md#tools-over-mcp). Workspace labels and export approval follow [Table rules](datacube.md#table-rules).

Conversation routes list at most 100 owned records, including archived records, in descending update order with ID as the tie-breaker. This is a listing limit, not a limit on stored chats. Create and rename accept nonblank titles of 1 to 200 characters and preserve the supplied title. Missing or foreign identifiers return 404. Archive retains history, deletes conversation files and refuses a running or paused run. Clearing the archive flag restores the conversation without restoring its deleted files. Runs and file operations require an active conversation. Sign-out preserves all records. Upload state and classification follow [Table rules](datacube.md#table-rules). Downloads record linked metadata without names or bytes.

`GET /api/conversations/:id/run` returns the owned latest run and approval status. A paused run can resume after its recorded approval is approved or rejected. Resume binds the same conversation, run and approval, restores saved state and reads the decision from the database.

## Browser and MCP access

The single `/summarize` shell is available with the development page disabled. It contains no source content before sign-in. Summary selection and uploads require the real session and allowed Origin. Source permissions apply before a model call. Browser controls retain only the selected source or owned File object and clear results on failure or sign-out.

HTTP MCP uses its own short-lived run credential. A session cookie alone is refused.
The token binds an enabled account and a running, unarchived owned conversation;
current roles load again for each request and tool call. Only its SHA-256 digest is
stored, and replacement revokes the previous value. Expiry, pause, run end and
cancellation stop later use. The browser never receives this credential. Tokens remain
outside model arguments and server logs. Tool metadata cannot replace the trusted
user, conversation, run or parent-call context.

## Workspace tokens

A workspace token lets owned Python call the same HTTP routes as the browser, as its account. A signed-in browser session or the command line issues one with `POST /api/me/workspace-tokens`; `craft call accounts.create_workspace_token --as <username> '{"label": "notebook"}'` does the same for provisioning. The response shows the token once. It lasts 1 to 168 hours, 12 by default.

| Rule | Behavior |
|---|---|
| Sending | `Authorization: Bearer <token>` on `/api` requests, without a session cookie. A request with both returns `401` |
| Reach | Routes whose operation is a read tool in the agent catalog (effect `NONE`, not `ask_agent`) and `GET /api/me`. Every other route, including token management, writes, approvals and conversations, returns `403`. Operations called inside a permitted route are not checked again |
| Identity | The token resolves to its enabled account and current roles on every request; mission and silo grants apply as for the browser |
| Logging | Each call is logged with origin `workspace` and the token identifier, and no token value |
| Ending | `DELETE /api/me/workspace-tokens/:id`, expiry or a disabled account stops later use with `401` |
| Storage | 32 random bytes, stored only as a SHA-256 digest with label, creation, expiry, last use (updated at most once a minute) and revocation times |

The workspace image reads the token from `CRAFT_TOKEN_FILE` (default `/run/craft/workspace-token`), a private file outside `USER_ROOT`, or from `craft.login()` for the current kernel only. `CRAFT_API_URL` names the server origin. Workspaces started through JupyterHub get no token file yet, so a notebook calls `craft.login()` with a token the person made.

## Workspace sign-in

JupyterLab signs in through JupyterHub with craft-id, the same account as CRAFT. Hub users are named by CRAFT user ID; CRAFT links a person's Hub user to their craft-id account on their first start, and the Hub refuses anyone without that link. CRAFT itself reaches the Hub with one service token (`CRAFT_HUB_TOKEN`), which never expires, is rotated by hand and is never logged. It may start, stop and read servers, and create users and groups only to make that link ([workspace](workspace.md#workspaces-through-jupyterhub)).

[Identity and workspace ownership](identity.md) records the stable ownership decision, separate credential lifecycles and migration/test gates. The [table reference](identity.md) distinguishes SQL conversation attachments from personal Jupyter files.
