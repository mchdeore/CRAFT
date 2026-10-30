"""Trusted identity and permissions, independent of request bodies."""

import hashlib
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.config.settings import Settings
from app.infrastructure.errors import Forbidden, NotSignedIn
from app.infrastructure.tokens import read_token

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
    """The caller a request signs in as: a session cookie, or a `workspace` bearer token, both
    resolved through accounts with the account's current roles; anonymous otherwise."""
    from app.accounts.handlers import resolve_session, resolve_token

    caller = anonymous_http(request)
    token = request.cookies.get("__Host-craft_session")
    bearer = request.headers.get("authorization")
    if bearer is not None:
        # A workspace token never combines with a browser session.
        hashed = None if token else bearer_digest(bearer)
        if hashed is None:
            return caller
        return resolve_token(caller, hashed, "workspace")
    raw = read_token(token) if token else None
    if raw is None:
        return caller
    return resolve_session(caller, hashlib.sha256(raw).digest())


def bearer_digest(header: str | None) -> bytes | None:
    """The SHA-256 digest of a well-formed `Bearer <token>` header: 32 random bytes as unpadded
    base64url. None for anything else. Only the digest leaves here."""
    if not isinstance(header, str) or not header.startswith("Bearer "):
        return None
    raw = read_token(header[7:])
    return None if raw is None else hashlib.sha256(raw).digest()
