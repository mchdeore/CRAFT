"""Account administration entry points for local mission grants."""

from typing import Annotated, Literal
from uuid import UUID

import msgspec
from starlette.responses import RedirectResponse, Response

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
    username: Annotated[str, msgspec.Meta(min_length=1)]
    password: Annotated[str, msgspec.Meta(min_length=1)]


Language = Literal["en", "fr"]


class Me(Shape):
    id: UUID
    username: str
    display_name: str
    roles: list[Literal["member", "admin"]]
    language: Language
    email: str | None = None


class MeChange(Shape):
    language: Language


class NewUser(Shape):
    username: str
    display_name: str
    # A password is for tests' local sign-in; with an email, people sign in at the provider.
    password: str | None = None
    email: str | None = None


class AccessApproval(Shape):
    email: str


class UserRef(Shape):
    username: str


class ResetPassword(Shape):
    username: str
    password: str


@operation(PUBLIC, post="/session")
def sign_in(caller: Caller, request: SignIn) -> Me:
    """Sign in with a local account."""
    return service.sign_in(caller, request)


class SignInStart(Shape):
    return_to: str | None = None


class SignInAnswer(Shape):
    # The provider's own query names, which are snake_case.
    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = msgspec.field(default=None, name="error_description")
    session_state: str | None = msgspec.field(default=None, name="session_state")


class SignedOut(Shape):
    logout_url: str | None


@operation(PUBLIC, get="/auth/login")
def auth_login(caller: Caller, request: SignInStart) -> Response:
    """Send the browser to the identity provider; `returnTo` is where it comes back to."""
    return RedirectResponse(service.start_sign_in(caller, request.return_to), 302)


@operation(PUBLIC, get="/auth/callback")
def auth_callback(caller: Caller, request: SignInAnswer) -> Response:
    """The provider's redirect back: on success a session cookie, then on to `returnTo`."""
    from app.infrastructure.operations.context import transport_request

    cookie = transport_request.get().cookies.get("__Host-craft_signin")
    target, problem = service.finish_sign_in(caller, cookie, request.state, request.code)
    if problem:
        # The page at the target reads this to explain; nothing about the person is in it.
        base = target.split("/", 3)[:3] if "://" in target else []
        target = ("/".join(base) if base else "") + "/?signIn=" + problem
    return RedirectResponse(target, 302)


@operation(SIGNED_IN, post="/auth/logout")
def auth_logout(caller: Caller) -> SignedOut:
    """End the CRAFT session; the browser then visits `logoutUrl` to end the provider's."""
    service.sign_out(caller)
    return SignedOut(logout_url=service.provider_logout(caller))


@operation(SIGNED_IN, delete="/session")
def sign_out(caller: Caller) -> None:
    """Revoke the current sign-in session."""
    service.sign_out(caller)


@operation(SIGNED_IN, get="/me")
def me(caller: Caller) -> Me:
    """Return the current authenticated account and its trusted roles."""
    return current(caller)


@operation(SIGNED_IN, patch="/me")
def change_me(caller: Caller, request: MeChange) -> Me:
    """Change the current account's own settings; `language` is the reply language."""
    service.change_language(caller, request.language)
    return current(caller)


def reply_language(caller: Caller) -> str:
    """The caller's saved reply language, for features that answer them."""
    return service.language(caller)


def current(caller: Caller) -> Me:
    from sqlalchemy import select

    from app.accounts import storage
    from app.infrastructure.database.unit_of_work import unit_of_work
    from app.infrastructure.errors import NotSignedIn

    if caller.sessions is None:
        raise NotSignedIn()
    with unit_of_work(caller.sessions, read_only=True) as session:
        row = (
            session.execute(
                select(storage.users).where(
                    storage.users.c.id == caller.user_id, storage.users.c.disabled_at.is_(None)
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise NotSignedIn()
    return msgspec.convert(
        {
            "id": row["id"],
            "username": row["username"],
            "displayName": row["display_name"],
            "roles": sorted(caller.roles),
            "language": row["language"],
            "email": row["email"],
        },
        Me,
    )


@operation("accounts.manage")
def add(caller: Caller, request: NewUser) -> None:
    service.add(caller, request)


@operation("accounts.manage")
def approve(caller: Caller, request: AccessApproval) -> Me:
    """Let in someone who signed in at the provider and was told "no access yet"."""
    return service.approve(caller, request.email)


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


class NewWorkspaceToken(Shape):
    label: Annotated[str, msgspec.Meta(min_length=1, max_length=80)]
    hours: Annotated[int, msgspec.Meta(ge=1, le=168)] = 12


class WorkspaceTokenRef(Shape):
    id: UUID


class WorkspaceToken(Shape):
    id: UUID
    label: str
    created_at: str
    expires_at: str
    last_used_at: str | None
    revoked_at: str | None


class IssuedWorkspaceToken(WorkspaceToken):
    token: str


@operation(SIGNED_IN, post="/me/workspace-tokens")
def create_workspace_token(caller: Caller, request: NewWorkspaceToken) -> IssuedWorkspaceToken:
    """Issue a read-only token for owned Python; the token is shown once."""
    return IssuedWorkspaceToken(**service.create_workspace_token(caller, request))


@operation(SIGNED_IN, get="/me/workspace-tokens")
def list_workspace_tokens(caller: Caller) -> list[WorkspaceToken]:
    """List the account's workspace tokens without their secrets."""
    return [WorkspaceToken(**row) for row in service.list_workspace_tokens(caller)]


@operation(SIGNED_IN, delete="/me/workspace-tokens/{id}")
def revoke_workspace_token(caller: Caller, request: WorkspaceTokenRef) -> None:
    """Revoke one of the account's workspace tokens."""
    service.revoke_workspace_token(caller, request.id)


def prune_expired(session) -> None:
    """Delete expired sign-in sessions and workspace tokens in the caller's transaction."""
    service.prune_expired(session)
