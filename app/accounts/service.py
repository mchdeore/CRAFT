"""Short transactions for account administration, sign-in at the identity provider, browser
sessions (resolve a cookie's digest to its account) and personal tokens (issue, list, revoke, and
resolve a token's digest to its account)."""

import hashlib
import logging
from dataclasses import replace
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.accounts import storage
from app.infrastructure import access
from app.infrastructure.access import Caller
from app.infrastructure.database.metadata import metadata
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import (
    ApiError,
    Conflict,
    InvalidInput,
    NotFound,
    NotSignedIn,
)
from app.infrastructure.grants import insert_grant, remove_grant
from app.infrastructure.operations.context import current_call, session_cookie, signin_cookie
from app.infrastructure.provider.simulated import SILOS
from app.infrastructure.shapes import FieldError
from app.infrastructure.tokens import new_token, read_token

logger = logging.getLogger("craft.accounts")


def change(caller: Caller, request, remove: bool) -> None:
    if caller.sessions is None:
        raise NotFound()
    if request.silo is not None and request.silo not in SILOS:
        raise InvalidInput()
    with unit_of_work(caller.sessions) as session:
        users = metadata.tables["users"]
        user = session.execute(
            select(users.c.id).where(users.c.username == request.username.lower())
        ).scalar_one_or_none()
        if user is None:
            raise NotFound()
        if remove:
            remove_grant(session, user, request.mission, request.silo)
        else:
            insert_grant(session, user, request.mission, request.silo)


def open_session(caller: Caller, session, user) -> list:
    """A new browser session for `user` in the caller's transaction; the cookie is set on reply."""
    assert caller.settings is not None
    at = access.now()
    raw, cookie = new_token()
    identifier = session.execute(
        storage.auth_sessions.insert()
        .values(
            user_id=user,
            token_sha256=hashlib.sha256(raw).digest(),
            created_at=at,
            last_seen_at=at,
            idle_expires_at=at + timedelta(hours=caller.settings.session_idle_hours),
            expires_at=at + timedelta(hours=caller.settings.session_absolute_hours),
        )
        .returning(storage.auth_sessions.c.id)
    ).scalar_one()
    assigned = storage.roles_for(session, user)
    context = current_call.get()
    if context is not None:
        context.caller = replace(
            caller, user_id=user, roles=frozenset(assigned), auth_session_id=identifier
        )
    session_cookie(
        cookie,
        caller.settings.session_absolute_hours * 3600,
        cross_origin_browser=caller.settings.cross_origin_browser,
    )
    return assigned


FLOW_LIFETIME = timedelta(minutes=10)


def return_target(settings, value: str | None) -> str:
    """A same-origin path, or a URL on an allowed browser origin; anything else is `/`."""
    from urllib.parse import urlsplit

    value = value or "/"
    if value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "/"
    origins = {origin.strip() for origin in settings.allowed_origins.split(",")}
    return value if f"{parsed.scheme}://{parsed.netloc}" in origins and not parsed.username else "/"


def start_sign_in(caller: Caller, return_to: str | None) -> str | None:
    """Remember the round trip under a fresh cookie and return the provider's authorize URL;
    None when the provider is unreachable or misconfigured."""
    from app.accounts import oidc

    settings = caller.settings
    if caller.sessions is None or settings is None or not settings.oidc_authority:
        raise NotFound()
    try:
        url, flow = oidc.begin(settings)
    except ApiError as error:
        logger.warning("sign-in failed", extra={"fields": {"error_type": type(error).__name__}})
        return None
    raw, flow_cookie = new_token()
    at = access.now()
    with unit_of_work(caller.sessions) as session:
        session.execute(
            storage.sign_in_flows.delete().where(storage.sign_in_flows.c.expires_at < at)
        )
        session.execute(
            storage.sign_in_flows.insert().values(
                id_sha256=hashlib.sha256(raw).digest(),
                flow=flow,
                return_to=return_target(settings, return_to),
                expires_at=at + FLOW_LIFETIME,
            )
        )
    signin_cookie(flow_cookie, FLOW_LIFETIME)
    return url


def finish_sign_in(caller: Caller, cookie: str | None, state, code) -> tuple[str, str | None]:
    """Check the provider's answer and open a session: (where to go, problem or None).

    Problems: `failed` (the round trip or token did not check out) or `no-access` (a real person
    CRAFT has not let in yet; their request is recorded for `craft users approve`).
    """
    from app.accounts import oidc

    settings = caller.settings
    if caller.sessions is None or settings is None or not settings.oidc_authority:
        raise NotFound()
    signin_cookie("", timedelta(0))
    raw = read_token(cookie or "")
    if raw is None:
        return "/", "failed"
    with unit_of_work(caller.sessions) as session:
        row = (
            session.execute(
                storage.sign_in_flows.delete()
                .where(storage.sign_in_flows.c.id_sha256 == hashlib.sha256(raw).digest())
                .returning(storage.sign_in_flows)
            )
            .mappings()
            .first()
        )
    if row is None or row["expires_at"] <= access.now():
        return "/", "failed"
    target = row["return_to"]
    try:
        claims = oidc.finish(settings, row["flow"], state, code)
    except ApiError as error:  # SignInFailed, or the provider unreachable or misconfigured
        logger.warning("sign-in failed", extra={"fields": {"error_type": type(error).__name__}})
        return target, "failed"
    tenant, oid = claims["tid"], claims["oid"]
    email = str(claims.get("email") or claims.get("preferred_username") or "").lower() or None
    with unit_of_work(caller.sessions) as session:
        user = storage.linked(session, tenant, oid, email)
        if user is None or user["disabled_at"] is not None:
            if user is None:
                storage.request_access(session, tenant, oid, email, str(claims.get("name") or ""))
            return target, "no-access"
        open_session(caller, session, user["id"])
    return target, None


def provider_logout(caller: Caller) -> str | None:
    """Where the browser goes to end the provider's own sign-in, after the session is revoked;
    None when the provider is unreachable or misconfigured, so sign-out still completes."""
    from urllib.parse import urlsplit

    from app.accounts import oidc

    settings = caller.settings
    if settings is None or not settings.oidc_authority:
        return None
    redirect = urlsplit(settings.oidc_redirect_uri)
    try:
        return oidc.logout_url(settings, f"{redirect.scheme}://{redirect.netloc}/")
    except ApiError as error:
        logger.warning(
            "provider sign-out skipped", extra={"fields": {"error_type": type(error).__name__}}
        )
        return None


def change_language(caller: Caller, language: str) -> None:
    if caller.sessions is None or caller.user_id is None:
        raise NotSignedIn()
    with unit_of_work(caller.sessions) as session:
        storage.set_language(session, caller.user_id, language)


def language(caller: Caller) -> str:
    """The caller's reply language; `en` when there is no signed-in user."""
    if caller.sessions is None or caller.user_id is None:
        return "en"
    with unit_of_work(caller.sessions, read_only=True) as session:
        return storage.language(session, caller.user_id) or "en"


def signed_in_account(caller: Caller):
    """The caller's own account row; NotSignedIn when it is gone or disabled."""
    if caller.sessions is None:
        raise NotSignedIn()
    with unit_of_work(caller.sessions, read_only=True) as session:
        row = storage.enabled_user(session, caller.user_id)
    if row is None:
        raise NotSignedIn()
    return row


def sign_out(caller: Caller) -> None:
    if caller.sessions is None or caller.user_id is None:
        raise NotSignedIn()
    with unit_of_work(caller.sessions) as session:
        storage.revoke_sessions(
            session, caller.user_id, access.now(), "sign_out", caller.auth_session_id
        )
    session_cookie(
        "",
        0,
        cross_origin_browser=caller.settings.cross_origin_browser if caller.settings else False,
    )


def add(caller: Caller, request) -> None:
    if caller.sessions is None or caller.settings is None:
        raise NotFound()
    email = request.email.strip().lower()
    if not email:
        raise InvalidInput()
    with unit_of_work(caller.sessions) as session:
        if storage.account(session, request.username.lower()) or (
            email and storage.by_email(session, email)
        ):
            raise Conflict()
        user = session.execute(
            storage.users.insert()
            .values(
                username=request.username.lower(), display_name=request.display_name, email=email
            )
            .returning(storage.users.c.id)
        ).scalar_one()
        session.execute(storage.user_roles.insert().values(user_id=user, role="member"))


def approve(caller: Caller, email: str):
    """Turn an access request into a linked member; the username is the email's local part."""
    from app.accounts.handlers import Me

    if caller.sessions is None:
        raise NotFound()
    email = email.strip().lower()
    with unit_of_work(caller.sessions) as session:
        pending = storage.take_request(session, email)
        if pending is None:
            raise NotFound()
        if storage.by_email(session, email):
            raise Conflict()
        username = email.partition("@")[0]
        if storage.account(session, username):
            username = email
        user = (
            session.execute(
                storage.users.insert()
                .values(
                    username=username,
                    display_name=pending["name"] or username,
                    email=email,
                    idp_tenant_id=pending["tenant_id"],
                    idp_object_id=pending["object_id"],
                )
                .returning(storage.users)
            )
            .mappings()
            .one()
        )
        session.execute(storage.user_roles.insert().values(user_id=user["id"], role="member"))
    return Me(
        id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        roles=["member"],
        language=user["language"],
        email=email,
    )


def list_users(caller: Caller):
    from app.accounts.handlers import Me

    if caller.sessions is None:
        raise NotFound()
    with unit_of_work(caller.sessions, read_only=True) as session:
        return [
            Me(
                id=row["id"],
                username=row["username"],
                display_name=row["display_name"],
                roles=storage.roles_for(session, row["id"]),
                language=row["language"],
                email=row["email"],
            )
            for row in session.execute(
                select(storage.users).order_by(storage.users.c.username)
            ).mappings()
        ]


def _locked_user_id(session, username: str):
    """The account's ID, its row locked for this transaction; NotFound when there is none."""
    user = storage.account(session, username.lower(), lock=True)
    if user is None:
        raise NotFound()
    return user["id"]


def promote(caller: Caller, username: str) -> None:
    if caller.sessions is None:
        raise NotFound()
    with unit_of_work(caller.sessions) as session:
        user_id = _locked_user_id(session, username)
        session.execute(
            insert(storage.user_roles)
            .values(user_id=user_id, role="admin")
            .on_conflict_do_nothing()
        )


def end_sessions(caller: Caller, username: str) -> None:
    """End the account's browser sessions; craft-id keeps its password."""
    if caller.sessions is None:
        raise NotFound()
    with unit_of_work(caller.sessions) as session:
        user_id = _locked_user_id(session, username)
        storage.revoke_sessions(session, user_id, access.now(), "admin_sign_out")


def disable(caller: Caller, username: str) -> None:
    """Stop the account signing in and end its sessions."""
    if caller.sessions is None:
        raise NotFound()
    with unit_of_work(caller.sessions) as session:
        user_id = _locked_user_id(session, username)
        at = access.now()
        session.execute(
            storage.users.update().where(storage.users.c.id == user_id).values(disabled_at=at)
        )
        storage.revoke_sessions(session, user_id, at, "disabled")


WORKSPACE_TOKEN_ORIGINS = frozenset({"http", "python"})
# Default and longest lifetime in hours by kind; the table checks the longest too.
TOKEN_HOURS = {"workspace": (12, 168), "mcp": (168, 720)}


def _token_row(row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "label": row["label"],
        "created_at": row["created_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat(),
        "last_used_at": row["last_used_at"].isoformat() if row["last_used_at"] else None,
        "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
    }


def resolve_token(caller: Caller, digest: bytes, kind: str, username: str | None = None) -> Caller:
    """The caller a personal token of this kind signs in as, with the account's current roles;
    the caller unchanged when no live token matches. Its calls are logged with the kind as
    their origin and the token's ID, never its value."""
    if caller.sessions is None:
        return caller
    at = access.now()
    with unit_of_work(caller.sessions, read_only=True) as session:
        row = storage.live_token(session, digest, kind, at, username)
        if row is None:
            return caller
        roles = frozenset(storage.roles_for(session, row["user_id"]))
    every = timedelta(seconds=60)
    if row["last_used_at"] is None or at - row["last_used_at"] >= every:
        with unit_of_work(caller.sessions) as session:
            storage.touch_token(session, row["id"], at, every)
    return replace(
        caller, user_id=row["user_id"], roles=roles, origin=kind, workspace_token_id=row["id"]
    )


def resolve_session(caller: Caller, digest: bytes) -> Caller:
    """The caller a browser session signs in as, with the account's current roles; the caller
    unchanged when no live session matches. Use extends the idle expiry, written at most once a
    minute and never past the absolute expiry."""
    if caller.sessions is None or caller.settings is None:
        return caller
    at = access.now()
    with unit_of_work(caller.sessions, read_only=True) as session:
        row = storage.live_session(session, digest, at)
        if row is None:
            return caller
        roles = frozenset(storage.roles_for(session, row["user_id"]))
    every = timedelta(seconds=60)
    if at - row["last_seen_at"] >= every:
        idle_until = min(
            at + timedelta(hours=caller.settings.session_idle_hours), row["expires_at"]
        )
        with unit_of_work(caller.sessions) as session:
            storage.touch_session(session, row["id"], at, every, idle_until)
    return replace(caller, user_id=row["user_id"], roles=roles, auth_session_id=row["id"])


def resolve_account(caller: Caller, username: str) -> Caller:
    """The caller an enabled account acts as on this machine, with its current roles; the
    caller unchanged when the account is unknown or disabled."""
    if caller.sessions is None:
        return caller
    with unit_of_work(caller.sessions, read_only=True) as session:
        user = storage.account(session, username.lower())
        if user is None or user["disabled_at"] is not None:
            return caller
        roles = frozenset(storage.roles_for(session, user["id"]))
    return replace(caller, user_id=user["id"], roles=roles)


def _token_owner(caller: Caller):
    from app.infrastructure.errors import Forbidden

    if caller.sessions is None or caller.user_id is None:
        raise NotSignedIn()
    # Only a signed-in browser session or the local command line manages tokens.
    if caller.origin not in WORKSPACE_TOKEN_ORIGINS:
        raise Forbidden()
    return caller.sessions


def create_workspace_token(caller: Caller, request) -> dict:
    sessions = _token_owner(caller)
    default, longest = TOKEN_HOURS[request.kind]
    hours = default if request.hours is None else request.hours
    if hours > longest:
        raise InvalidInput(fields=[FieldError(path="$.hours", message=f"At most {longest}.")])
    issued = _issue_workspace_token(sessions, caller.user_id, request.kind, request.label, hours)
    if issued is None:
        raise NotSignedIn()
    return issued


# A workspace started through JupyterHub swaps its own server's Hub token for one of these.
HUB_TOKEN_LABEL = "jupyterhub"
HUB_TOKEN_HOURS = 12


async def workspace_token_from_hub(caller: Caller) -> dict:
    """Issue a workspace token to the person whose server holds the presented Hub token.

    The token comes in ``Authorization: token <Hub token>`` and is checked by the Hub itself
    (``JupyterHub.owner``); it is never stored or logged. Any refusal is ``NotSignedIn``.
    """
    from starlette.concurrency import run_in_threadpool

    from app.infrastructure import hub
    from app.infrastructure.operations.context import transport_request

    request = transport_request.get()
    header = request.headers.get("authorization", "") if request is not None else ""
    presented = header.removeprefix("token ") if header.startswith("token ") else ""
    found = hub.from_settings(caller.settings)
    if found is None or caller.sessions is None or not hub.TOKEN.fullmatch(presented):
        raise NotSignedIn()
    user_id = await found.owner(presented)
    if user_id is None:
        raise NotSignedIn()
    # The call log names the account the token is issued to.
    context = current_call.get()
    if context is not None:
        context.caller = replace(context.caller, user_id=user_id)
    issued = await run_in_threadpool(
        _issue_workspace_token,
        caller.sessions,
        user_id,
        "workspace",
        HUB_TOKEN_LABEL,
        HUB_TOKEN_HOURS,
    )
    if issued is None:
        raise NotSignedIn()
    return issued


def _issue_workspace_token(sessions, user_id, kind: str, label: str, hours: int) -> dict | None:
    """A new personal token of ``kind`` for an enabled account, shown once; None when the
    account can't sign in."""
    at = access.now()
    raw, token = new_token()
    with unit_of_work(sessions) as session:
        enabled = session.execute(
            select(storage.users.c.id).where(
                storage.users.c.id == user_id, storage.users.c.disabled_at.is_(None)
            )
        ).first()
        if enabled is None:
            return None
        row = (
            session.execute(
                storage.workspace_tokens.insert()
                .values(
                    user_id=user_id,
                    token_sha256=hashlib.sha256(raw).digest(),
                    kind=kind,
                    label=label,
                    created_at=at,
                    expires_at=at + timedelta(hours=hours),
                )
                .returning(storage.workspace_tokens)
            )
            .mappings()
            .one()
        )
    return {**_token_row(row), "token": token}


def list_workspace_tokens(caller: Caller) -> list[dict]:
    sessions = _token_owner(caller)
    tokens = storage.workspace_tokens
    with unit_of_work(sessions, read_only=True) as session:
        rows = (
            session.execute(
                select(tokens)
                .where(tokens.c.user_id == caller.user_id)
                .order_by(tokens.c.created_at.desc())
                .limit(100)
            )
            .mappings()
            .all()
        )
    return [_token_row(row) for row in rows]


def revoke_workspace_token(caller: Caller, token_id) -> None:
    sessions = _token_owner(caller)
    tokens = storage.workspace_tokens
    with unit_of_work(sessions) as session:
        found = session.execute(
            tokens.update()
            .where(tokens.c.id == token_id, tokens.c.user_id == caller.user_id)
            .values(revoked_at=func.coalesce(tokens.c.revoked_at, access.now()))
            .returning(tokens.c.id)
        ).first()
    if found is None:
        raise NotFound()


def prune_expired(session) -> None:
    """Delete sign-in sessions and workspace tokens past their expiry, as of now."""
    storage.prune_expired(session, access.now())
