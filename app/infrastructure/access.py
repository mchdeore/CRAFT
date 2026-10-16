"""Trusted identity and permissions, independent of request bodies."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.config.settings import Settings
from app.infrastructure.errors import Forbidden, NotSignedIn

PUBLIC = "public"
SIGNED_IN = "signed_in"
PERMISSIONS = {"accounts.manage": frozenset({"admin"})}


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


def require(caller: Caller, access: str) -> None:
    if not isinstance(caller, Caller):
        raise NotSignedIn()
    if access == PUBLIC:
        return
    if caller.user_id is None:
        raise NotSignedIn()
    if access != SIGNED_IN and not caller.roles.intersection(PERMISSIONS.get(access, frozenset())):
        raise Forbidden()


def resolve_http(request: Any) -> Caller:
    """Only transport authentication may populate identity. Sessions arrive separately."""
    return Caller(
        sessions=request.app.state.sessions,
        settings=request.app.state.settings,
        migration_head=request.app.state.migration_head,
        origin="http",
    )
