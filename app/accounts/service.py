"""Short transactions for local account administration."""

import base64
import hashlib
import logging
import secrets
from collections import defaultdict, deque
from datetime import timedelta
from functools import lru_cache
from threading import Lock

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select

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
    RateLimited,
)
from app.infrastructure.grants import insert_grant, remove_grant
from app.infrastructure.operations.context import current_call, session_cookie, signin_cookie
from app.infrastructure.provider.simulated import SILOS

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


PASSWORDS = PasswordHasher()
ATTEMPTS = defaultdict(deque)
ATTEMPT_LOCK = Lock()


@lru_cache(maxsize=1)
def dummy_hash() -> str:
    return PASSWORDS.hash("unused-synthetic-dummy-password")


def sign_in(caller: Caller, request):
    if caller.sessions is None or caller.settings is None:
        raise NotSignedIn()
    if not caller.settings.local_password_signin:
        raise NotFound()  # people sign in at the identity provider (auth_login)
    username = request.username.lower()
    at = access.now()
    with ATTEMPT_LOCK:
        attempts = ATTEMPTS[username]
        while attempts and (at - attempts[0]).total_seconds() >= 60:
            attempts.popleft()
        if len(attempts) >= caller.settings.sign_in_attempts_per_minute:
            raise RateLimited()
        attempts.append(at)
    with unit_of_work(caller.sessions, read_only=True) as session:
        user = storage.account(session, username)
        digest = storage.credentials(session, user["id"]) if user else None
    try:
        valid = PASSWORDS.verify(digest or dummy_hash(), request.password)
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid or digest is None or user is None or user["disabled_at"] is not None:
        raise NotSignedIn()
    # Hashing and verification finish before the write transaction.
    replacement = PASSWORDS.hash(request.password) if PASSWORDS.check_needs_rehash(digest) else None
    with unit_of_work(caller.sessions) as session:
        current = storage.account(session, username, lock=True)
        if (
            current is None
            or current["disabled_at"] is not None
            or storage.credentials(session, current["id"]) != digest
        ):
            raise NotSignedIn()
        if replacement:
            session.execute(
                storage.local_credentials.update()
                .where(storage.local_credentials.c.user_id == user["id"])
                .values(password_hash=replacement, updated_at=at)
            )
        assigned = open_session(caller, session, user["id"])
    from app.accounts.handlers import Me

    return Me(
        id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        roles=assigned,
        language=user["language"],
        email=user["email"],
    )


def open_session(caller: Caller, session, user) -> list:
    """A new browser session for `user` in the caller's transaction; the cookie is set on reply."""
    assert caller.settings is not None
    at = access.now()
    raw = secrets.token_bytes(32)
    cookie = base64.urlsafe_b64encode(raw).decode().rstrip("=")
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
        from dataclasses import replace

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


def start_sign_in(caller: Caller, return_to: str | None) -> str:
    """Remember the round trip under a fresh cookie and return the provider's authorize URL."""
    from app.accounts import oidc

    settings = caller.settings
    if caller.sessions is None or settings is None or not settings.oidc_authority:
        raise NotFound()
    url, flow = oidc.begin(settings)
    raw = secrets.token_bytes(32)
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
    signin_cookie(base64.urlsafe_b64encode(raw).decode().rstrip("="), FLOW_LIFETIME)
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
    try:
        raw = base64.urlsafe_b64decode((cookie or "") + "=" * (-len(cookie or "") % 4))
    except ValueError:
        raw = b""
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
    if row is None or len(raw) != 32 or row["expires_at"] <= access.now():
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
    """Where the browser goes to end the provider's own sign-in, after the session is revoked."""
    from urllib.parse import urlsplit

    from app.accounts import oidc

    settings = caller.settings
    if settings is None or not settings.oidc_authority:
        return None
    redirect = urlsplit(settings.oidc_redirect_uri)
    return oidc.logout_url(settings, f"{redirect.scheme}://{redirect.netloc}/")


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
    email = request.email.strip().lower() if request.email else None
    if request.password is None and email is None:
        raise InvalidInput()
    if request.password is not None and len(request.password) < caller.settings.password_min_length:
        raise InvalidInput()
    password_hash = PASSWORDS.hash(request.password) if request.password is not None else None
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
        if password_hash is not None:
            session.execute(
                storage.local_credentials.insert().values(user_id=user, password_hash=password_hash)
            )
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


def manage(caller: Caller, request, action: str) -> None:
    if caller.sessions is None:
        raise NotFound()
    password_hash = None
    if action == "reset":
        if caller.settings is None or len(request.password) < caller.settings.password_min_length:
            raise InvalidInput()
        password_hash = PASSWORDS.hash(request.password)
    with unit_of_work(caller.sessions) as session:
        user = storage.account(session, request.username.lower(), lock=True)
        if user is None:
            raise NotFound()
        at = access.now()
        if action == "promote":
            from sqlalchemy.dialects.postgresql import insert

            session.execute(
                insert(storage.user_roles)
                .values(user_id=user["id"], role="admin")
                .on_conflict_do_nothing()
            )
        elif action == "reset":
            from sqlalchemy.dialects.postgresql import insert

            statement = insert(storage.local_credentials).values(
                user_id=user["id"], password_hash=password_hash
            )
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[storage.local_credentials.c.user_id],
                    set_={"password_hash": password_hash, "updated_at": at},
                )
            )
            storage.revoke_sessions(session, user["id"], at, "password_reset")
        elif action == "disable":
            session.execute(
                storage.users.update()
                .where(storage.users.c.id == user["id"])
                .values(disabled_at=at)
            )
            storage.revoke_sessions(session, user["id"], at, "disabled")


WORKSPACE_TOKEN_ORIGINS = frozenset({"http", "python"})


def _token_row(row) -> dict:
    return {
        "id": row["id"],
        "label": row["label"],
        "created_at": row["created_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat(),
        "last_used_at": row["last_used_at"].isoformat() if row["last_used_at"] else None,
        "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
    }


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
    at = access.now()
    raw = secrets.token_bytes(32)
    with unit_of_work(sessions) as session:
        row = (
            session.execute(
                storage.workspace_tokens.insert()
                .values(
                    user_id=caller.user_id,
                    token_sha256=hashlib.sha256(raw).digest(),
                    label=request.label,
                    created_at=at,
                    expires_at=at + timedelta(hours=request.hours),
                )
                .returning(storage.workspace_tokens)
            )
            .mappings()
            .one()
        )
    return {**_token_row(row), "token": base64.urlsafe_b64encode(raw).decode().rstrip("=")}


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
