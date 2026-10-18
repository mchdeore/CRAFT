"""Bearer-only run authentication; current principals come through checked handlers."""

import base64
import hashlib
from contextvars import ContextVar
from dataclasses import replace

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from app.agent.handlers import RunTokenDigest, resolve_run_token
from app.infrastructure.access import anonymous_http
from app.infrastructure.errors import NotSignedIn

credential: ContextVar[tuple | None] = ContextVar("mcp_credential", default=None)


def digest(header):
    if not isinstance(header, str) or not header.startswith("Bearer "):
        raise NotSignedIn()
    value = header[7:]
    try:
        raw = base64.b64decode(value + "=" * ((-len(value)) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise NotSignedIn() from None
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value:
        raise NotSignedIn()
    return hashlib.sha256(raw).digest()


async def authenticate(request: Request):
    base = replace(anonymous_http(request), origin="mcp")
    hashed = digest(request.headers.get("authorization"))
    principal = await run_in_threadpool(resolve_run_token, base, RunTokenDigest(digest=hashed))
    return base, hashed, principal


async def current():
    entry = credential.get()
    if entry is None:
        raise NotSignedIn()
    base, hashed, _ = entry
    principal = await run_in_threadpool(resolve_run_token, base, RunTokenDigest(digest=hashed))
    caller = replace(
        base,
        user_id=principal.user,
        roles=frozenset(principal.roles),
        conversation_id=principal.conversation,
        run_id=principal.run,
        auth_session_id=principal.auth_session_id,
    )
    return caller, principal
