"""Bearer-only authentication at /mcp: a personal `mcp` token or an agent run token.

Each kind has a `TokenResolver` that turns the request's bearer into a `Credential`, or None when
the bearer is not of its kind. `RESOLVERS` tries the personal token first: a plain lookup, while
a run token resolves through the checked and logged run handler. The `Credential` then gives the
MCP server the caller for each message:

- `PersonalToken`: resolved once per HTTP request to its account, with current roles and
  grants; refused over its rate limit; reaches only the read tools (`token_allowed`).
- `RunToken`: re-resolved for each message through the run handler, unchanged.

A session cookie never signs in here. Only token digests are kept or passed on.
"""

from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Protocol

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from app.agent.handlers import RunPrincipal, RunTokenDigest, resolve_run_token
from app.infrastructure.access import Caller, anonymous_http, bearer_digest
from app.infrastructure.errors import NotSignedIn

# A personal MCP token makes at most this many tool calls a minute.
TOKEN_CALLS_PER_MINUTE = 60


class Credential(Protocol):
    """A bearer that signed in at /mcp, held for the rest of its HTTP request."""

    @property
    def caller(self) -> Caller:
        """Who signed in, as resolved when the request arrived; traffic counting records it."""
        ...

    def admit(self) -> None:
        """Refuse the request before any message runs, such as over a rate limit."""
        ...

    def current(self) -> tuple[Caller, RunPrincipal | None]:
        """The caller for one message, and for a run token its run principal."""
        ...


class TokenResolver(Protocol):
    """Resolves one kind of bearer token."""

    def __call__(self, base: Caller, digest: bytes, claimed: str | None) -> Credential | None:
        """The credential for this token digest, or None when it is not this kind's.

        `claimed` is the request's optional X-Craft-User; a kind may require it to match."""
        ...


credential: ContextVar[Credential | None] = ContextVar("mcp_credential", default=None)


@dataclass(frozen=True)
class PersonalToken:
    """An `mcp` token, resolved to its account for this request."""

    caller: Caller

    def admit(self) -> None:
        from app.infrastructure.operations.service import limit_token

        limit_token(self.caller, TOKEN_CALLS_PER_MINUTE, 60)

    def current(self) -> tuple[Caller, RunPrincipal | None]:
        return self.caller, None


@dataclass(frozen=True)
class RunToken:
    """An agent run token; its run and roles are read again for every message."""

    base: Caller
    digest: bytes
    caller: Caller

    def admit(self) -> None:
        pass

    def current(self) -> tuple[Caller, RunPrincipal | None]:
        return resolve_run(self.base, self.digest)


def resolve_run(base: Caller, digest: bytes) -> tuple[Caller, RunPrincipal]:
    """The caller of a current run's token through the checked run handler; else NotSignedIn."""
    principal = resolve_run_token(base, RunTokenDigest(digest=digest))
    caller = replace(
        base,
        user_id=principal.user,
        roles=frozenset(principal.roles),
        conversation_id=principal.conversation,
        run_id=principal.run,
        auth_session_id=principal.auth_session_id,
    )
    return caller, principal


def personal_token(base: Caller, digest: bytes, claimed: str | None) -> PersonalToken | None:
    """An `mcp` token of an enabled account; with X-Craft-User, only that account's."""
    from app.accounts.handlers import resolve_token

    caller = resolve_token(base, digest, "mcp", claimed)
    return PersonalToken(caller) if caller.workspace_token_id is not None else None


def run_token(base: Caller, digest: bytes, claimed: str | None) -> RunToken | None:
    """A current run's token. X-Craft-User does not apply: run tokens are unchanged."""
    try:
        caller, _ = resolve_run(base, digest)
    except NotSignedIn:
        return None
    return RunToken(base, digest, caller)


RESOLVERS: tuple[TokenResolver, ...] = (personal_token, run_token)


def digest(header) -> bytes:
    """The digest of a well-formed bearer header; anything else is not signed in."""
    hashed = bearer_digest(header)
    if hashed is None:
        raise NotSignedIn()
    return hashed


async def authenticate(request: Request) -> Credential:
    """The request's credential, admitted; NotSignedIn when no resolver knows its bearer."""
    base = replace(anonymous_http(request), origin="mcp")
    hashed = digest(request.headers.get("authorization"))
    claimed = request.headers.get("x-craft-user")
    for resolve in RESOLVERS:
        found = await run_in_threadpool(resolve, base, hashed, claimed)
        if found is not None:
            await run_in_threadpool(found.admit)
            return found
    raise NotSignedIn()


async def current() -> tuple[Caller, RunPrincipal | None]:
    """The caller for the message being handled, from this request's credential."""
    entry = credential.get()
    if entry is None:
        raise NotSignedIn()
    return await run_in_threadpool(entry.current)
