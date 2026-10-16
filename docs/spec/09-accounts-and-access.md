# 9. Accounts and access

The command-line interface (CLI) creates local accounts. Agent-User Interaction (AG-UI) defines conversation messages. The application programming interface (API) uses Hypertext Transfer Protocol (HTTP). The 256-bit Secure Hash Algorithm (SHA-256) produces token digests. Structured Query Language (SQL) defines session rows.

## 9.1 Accounts

| Item | Now | Deferred |
|---|---|---|
| Sign-in | Local accounts with Argon2id password hashes, passwords of at least 12 characters and creation through the CLI. No sign-up, default account or email recovery | Microsoft Entra ID |
| Roles | `member` uses CRAFT; `admin` also manages accounts through `craft users promote`. Operation and resource permissions apply to both | Expanded role mapping and Entra group grants. New values in a database check constraint (`CHECK`) require a migration |
| Approvals | The requester decides an effectful action under section 3.4. The agent cannot decide | Expanded decision policy in section 12 |

Rehash passwords at sign-in when the `argon2-cffi` defaults change.

## 9.2 Cookie and session row

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

Tokens are 32 random bytes, replaced at every sign-in and stored only as SHA-256 digests. The `__Host-` prefix binds the cookie to the host; `HttpOnly` keeps it away from page scripts. Tokens never appear in logs, uniform resource locators (URLs) or bodies. Sign-in tokens stay out of browser storage. Section 7.2 defines the schema.

The front end forwards `/api` and `/health` to `http://localhost:8000` through its development server and uses the same origin. Cookie lookup runs in `app/infrastructure/access.py`. Handlers receive the trusted `Caller` and never read cookies.

## 9.3 Flows

| Flow | Server steps | Result |
|---|---|---|
| Sign in: `POST /api/session` | Rate-limit the username. Load the account and verify its hash; use a dummy hash for a missing account. Refuse disabled accounts. Create a new token and session with an eight-hour idle expiry and twelve-hour absolute expiry. Set the cookie. | `200 Me`; wrong username or password both give `401`; rate limit gives `429` |
| Each request | Hash the cookie token. Find an unrevoked session whose expiries are ahead. Load the enabled account and its current roles into `Caller`. | Trusted `Caller`, otherwise `401` |
| Keep alive | At most once a minute, update last-seen time and extend idle expiry by eight hours, capped at the absolute expiry. | One short update |
| Sign out: `DELETE /api/session` | Revoke the sign-in session with reason `sign_out` and clear the cookie. | `204`. Sign-out deletes nothing. The workspace stays with its conversation until the conversation is archived under section 7 |
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

## 9.4 Settings

| Setting | Default |
|---|---|
| `CRAFT_SESSION_IDLE_HOURS` | 8 |
| `CRAFT_SESSION_ABSOLUTE_HOURS` | 12 |
| `CRAFT_SIGN_IN_ATTEMPTS_PER_MINUTE` | 5 |
| `CRAFT_PASSWORD_MIN_LENGTH` | 12 |
| `CRAFT_ALLOWED_ORIGINS` | `http://localhost:8000` |
| `CRAFT_DEV_PAGE` | 0 (disabled) |

Settings validate at startup. `CRAFT_DEV_PAGE=1` mounts the single temporary `/dev` page for same-origin sign-in, current-account lookup, library search and sign-out. The page uses text nodes for document content and stores no session token. The keep-alive update extends idle expiry by eight hours, at most once a minute, and never beyond the twelve-hour absolute expiry.

## 9.5 Conversation storage

| Table | Holds | Written |
|---|---|---|
| `conversations` | Owner, title, archived | Create, rename, archive |
| `messages` | Numbered, append-only AG-UI messages: user, assistant, tool, and a `craft.answer` activity with citations and confidence; run | Each user message; each agent message at the end of a run |
| `workspace_files` | The conversation's files, uploaded or made by the agent; deleted when the conversation is archived | Upload, agent output, delete, export |
| `agent_runs` | Status, model, instructions version, run token digest | At run start, pause and end |
| `agent_sessions` | The agent's saved state, one row per conversation | Before and after each run |

Every access checks the owner; a conversation identifier grants no permission. History comes only from `messages`. CRAFT stores conversation history and agent state under section 7. Model response storage follows section 4.1; run tokens follow section 3.3. Workspace labels and export approval follow section 7.3.

## 9.6 Deferred access

| Later | What changes | What stays |
|---|---|---|
| Microsoft Entra ID sign-in | The sign-in route redirects to Entra ID; its tokens stay on the server | Cookie, session table, every operation |
| Local accounts retired | Local passwords disabled | User identifiers, linked by tenant and object identifier, never by email |
| Eight roles | Permission map entries and role rows | The single access check |
| A separate front-end origin | A cross-origin allowlist with credentials | Cookie, `Origin` check |

Entra sign-in uses OpenID Connect with tokens on the server. Accounts link by tenant and object identifier, never email. Local credentials are disabled after migration.
