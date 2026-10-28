"""Short transactions for local account administration."""

import base64
import hashlib
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
from app.infrastructure.errors import Conflict, InvalidInput, NotFound, NotSignedIn, RateLimited
from app.infrastructure.grants import insert_grant, remove_grant
from app.infrastructure.operations.context import current_call, session_cookie
from app.infrastructure.provider.simulated import SILOS


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
    raw = secrets.token_bytes(32)
    cookie = base64.urlsafe_b64encode(raw).decode().rstrip("=")
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
        identifier = session.execute(
            storage.auth_sessions.insert()
            .values(
                user_id=user["id"],
                token_sha256=hashlib.sha256(raw).digest(),
                created_at=at,
                last_seen_at=at,
                idle_expires_at=at + timedelta(hours=caller.settings.session_idle_hours),
                expires_at=at + timedelta(hours=caller.settings.session_absolute_hours),
            )
            .returning(storage.auth_sessions.c.id)
        ).scalar_one()
        assigned = storage.roles_for(session, user["id"])
    from app.accounts.handlers import Me

    context = current_call.get()
    if context is not None:
        from dataclasses import replace

        context.caller = replace(
            caller, user_id=user["id"], roles=frozenset(assigned), auth_session_id=identifier
        )
    session_cookie(
        cookie,
        caller.settings.session_absolute_hours * 3600,
        cross_origin_browser=caller.settings.cross_origin_browser,
    )
    return Me(
        id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        roles=assigned,
        language=user["language"],
    )


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
    if len(request.password) < caller.settings.password_min_length:
        raise InvalidInput()
    password_hash = PASSWORDS.hash(request.password)
    with unit_of_work(caller.sessions) as session:
        if storage.account(session, request.username.lower()):
            raise Conflict()
        user = session.execute(
            storage.users.insert()
            .values(username=request.username.lower(), display_name=request.display_name)
            .returning(storage.users.c.id)
        ).scalar_one()
        session.execute(
            storage.local_credentials.insert().values(user_id=user, password_hash=password_hash)
        )
        session.execute(storage.user_roles.insert().values(user_id=user, role="member"))


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
