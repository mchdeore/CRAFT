"""Account administration entry points for local mission grants."""

from typing import Annotated, Literal
from uuid import UUID

import msgspec

from app.accounts import service
from app.infrastructure.access import PUBLIC, SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape


class GrantMission(Shape):
    username: str
    mission: str
    silo: str | None = None


@operation("accounts.manage")
def grant(caller: Caller, request: GrantMission) -> None:
    """Grant a mission scope to an existing local account."""
    service.change(caller, request, False)


@operation("accounts.manage")
def revoke(caller: Caller, request: GrantMission) -> None:
    """Remove one mission scope from an existing local account."""
    service.change(caller, request, True)


class SignIn(Shape):
    username: Annotated[str, msgspec.Meta(min_length=1, max_length=100)]
    password: Annotated[str, msgspec.Meta(min_length=1)]


class Me(Shape):
    id: UUID
    username: str
    display_name: str
    roles: list[Literal["member", "admin"]]


class NewUser(Shape):
    username: str
    display_name: str
    password: str


class UserRef(Shape):
    username: str


class ResetPassword(Shape):
    username: str
    password: str


@operation(PUBLIC, post="/session")
def sign_in(caller: Caller, request: SignIn) -> Me:
    """Sign in with a local account."""
    return service.sign_in(caller, request)


@operation(SIGNED_IN, delete="/session")
def sign_out(caller: Caller) -> None:
    """Revoke the current sign-in session."""
    service.sign_out(caller)


@operation("accounts.manage")
def add(caller: Caller, request: NewUser) -> None:
    service.add(caller, request)


@operation("accounts.manage")
def list_users(caller: Caller) -> list[Me]:
    return service.list_users(caller)


@operation("accounts.manage")
def promote(caller: Caller, request: UserRef) -> None:
    service.manage(caller, request, "promote")


@operation("accounts.manage")
def reset(caller: Caller, request: ResetPassword) -> None:
    service.manage(caller, request, "reset")


@operation("accounts.manage")
def disable(caller: Caller, request: UserRef) -> None:
    service.manage(caller, request, "disable")
