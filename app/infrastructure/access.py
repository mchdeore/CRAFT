"""Trusted identity and permissions, independent of request bodies."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.config.settings import Settings
from app.infrastructure.errors import Forbidden, NotSignedIn

PUBLIC = "public"
SIGNED_IN = "signed_in"
PERMISSIONS = {
    "accounts.manage": frozenset({"admin"}),
    "library.manage": frozenset({"admin"}),
}


@dataclass(frozen=True, slots=True)
class Caller:
    user_id: UUID | None = None
    roles: frozenset[str] = field(default_factory=frozenset)
    auth_session_id: UUID | None = None
    sessions: sessionmaker[Session] | None = field(default=None, repr=False, compare=False)
    settings: Settings | None = field(default=None, repr=False, compare=False)
    migration_head: str | None = None
    origin: str = "python"
    conversation_id: UUID | None = None
    run_id: UUID | None = None
    tool_call_id: str | None = None
    workspace_token_id: UUID | None = None


def require(caller: Caller, access: str) -> None:
    if not isinstance(caller, Caller):
        raise NotSignedIn()
    if access == PUBLIC:
        return
    if caller.user_id is None:
        raise NotSignedIn()
    if access != SIGNED_IN and not caller.roles.intersection(PERMISSIONS.get(access, frozenset())):
        raise Forbidden()


def now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def anonymous_http(request: Any) -> Caller:
    return Caller(
        sessions=request.app.state.sessions,
        settings=request.app.state.settings,
        migration_head=request.app.state.migration_head,
        origin="http",
    )


def resolve_http(request: Any) -> Caller:
    """Resolve a session cookie's server digest, or a `workspace` token through accounts;
    reload current account roles every request."""
    import base64
    import hashlib
    from datetime import timedelta

    from sqlalchemy import select, update

    from app.infrastructure.database.metadata import metadata
    from app.infrastructure.database.unit_of_work import unit_of_work

    caller = anonymous_http(request)
    token = request.cookies.get("__Host-craft_session")
    bearer = request.headers.get("authorization")
    if bearer is not None:
        # A workspace token never combines with a browser session.
        hashed = None if token else bearer_digest(bearer)
        if hashed is None:
            return caller
        from app.accounts.handlers import resolve_token

        return resolve_token(caller, hashed, "workspace")
    if not token or caller.sessions is None or caller.settings is None:
        return caller
    try:
        raw = base64.b64decode(token + "=" * ((-len(token)) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        return caller
    if len(raw) != 32:
        return caller
    digest = hashlib.sha256(raw).digest()
    at = now()
    users = metadata.tables["users"]
    sessions = metadata.tables["auth_sessions"]
    roles = metadata.tables["user_roles"]
    with unit_of_work(caller.sessions, read_only=True) as session:
        row = (
            session.execute(
                select(sessions, users.c.disabled_at)
                .join(users, users.c.id == sessions.c.user_id)
                .where(
                    sessions.c.token_sha256 == digest,
                    sessions.c.revoked_at.is_(None),
                    sessions.c.idle_expires_at > at,
                    sessions.c.expires_at > at,
                    users.c.disabled_at.is_(None),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return caller
        assigned = frozenset(
            session.execute(select(roles.c.role).where(roles.c.user_id == row["user_id"])).scalars()
        )
    if (at - row["last_seen_at"]).total_seconds() >= 60:
        with unit_of_work(caller.sessions) as session:
            session.execute(
                update(sessions)
                .where(
                    sessions.c.id == row["id"],
                    sessions.c.last_seen_at <= at - timedelta(seconds=60),
                    sessions.c.revoked_at.is_(None),
                )
                .values(
                    last_seen_at=at,
                    idle_expires_at=min(
                        at + timedelta(hours=caller.settings.session_idle_hours), row["expires_at"]
                    ),
                )
            )
    from dataclasses import replace

    return replace(caller, user_id=row["user_id"], roles=assigned, auth_session_id=row["id"])


def bearer_digest(header: str | None) -> bytes | None:
    """The SHA-256 digest of a well-formed `Bearer <token>` header: 32 random bytes as unpadded
    base64url. None for anything else. Only the digest leaves here."""
    import base64
    import hashlib

    if not isinstance(header, str) or not header.startswith("Bearer "):
        return None
    value = header[7:]
    try:
        raw = base64.b64decode(value + "=" * ((-len(value)) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        return None
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value:
        return None
    return hashlib.sha256(raw).digest()
