"""Small official-MCP client seam; credentials never become tool arguments."""

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ListToolsResult


class Session(Protocol):
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult: ...

    async def list_tools(self) -> ListToolsResult: ...


class Craft:
    """Use an injected authenticated session; server authorization remains authoritative."""

    def __init__(self, session: Session):
        self._session = session

    async def tools(self) -> ListToolsResult:
        return await self._session.list_tools()

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        """Dispatch exactly once and preserve native errors/results and call provenance."""
        return await self._session.call_tool(
            name, arguments, meta={"craft/toolCallId": str(uuid4())}
        )

    async def search(self, query: str, **filters: Any) -> CallToolResult:
        return await self.call("search", {"query": query, **filters})

    async def document(self, item: str, version: int | None = None) -> CallToolResult:
        return await self.call("get_document", {"item": item, "version": version})

    async def skills(self, **paging: Any) -> CallToolResult:
        return await self.call("list_skills", paging)


@dataclass(slots=True)
class _RunBinding:
    url: str = field(repr=False)
    run_token: str = field(repr=False)
    run_id: str
    binding_id: str
    deadline: float
    expiry: asyncio.TimerHandle | None = field(default=None, repr=False)


_binding: _RunBinding | None = None


def _unbind_run(binding_id: str) -> bool:
    """Root cleanup cannot erase a later binding, including after pause/resume."""
    global _binding
    if _binding is None:
        return True
    if _binding.binding_id != binding_id:
        return False
    bound, _binding = _binding, None
    bound.run_token = ""
    if bound.expiry is not None:
        bound.expiry.cancel()
    return True


def _bind_run(
    url: str, run_token: str, run_id: str, seconds: float, *, binding_id: str | None = None
) -> None:
    """Called only by the controller's silent, authenticated kernel execution."""
    global _binding
    binding_id = run_id if binding_id is None else binding_id
    loop = asyncio.get_running_loop()
    target = urlsplit(url)
    if (
        target.scheme != "http"
        or target.hostname not in {"127.0.0.1", "localhost", "host.docker.internal"}
        or target.username is not None
        or target.password is not None
        or target.query
        or target.fragment
        or target.path != "/mcp"
        or not run_token
        or any(value.isspace() for value in run_token)
        or str(UUID(run_id)) != run_id
        or str(UUID(binding_id)) != binding_id
        or not math.isfinite(seconds)
        or seconds <= 0
    ):
        raise ValueError("Expected a root-provisioned active run binding")
    if _binding is not None and loop.time() >= _binding.deadline:
        _unbind_run(_binding.binding_id)
    if _binding is not None:
        raise RuntimeError("This kernel is already bound to an active CRAFT run")
    bound = _RunBinding(url, run_token, run_id, binding_id, loop.time() + seconds)
    _binding = bound
    bound.expiry = loop.call_later(seconds, _unbind_run, binding_id)


@asynccontextmanager
async def native() -> AsyncIterator[Craft]:
    """Use only the active capability injected into this kernel's memory by root.

    A standalone process or another kernel has no binding. This accessor never
    reads credentials from environment, files, notebooks or browser sessions.
    """
    bound = _binding
    if bound is None or asyncio.get_running_loop().time() >= bound.deadline:
        raise RuntimeError("This kernel has no active CRAFT run binding")
    async with asyncio.timeout_at(bound.deadline):
        async with connect(bound.url, run_token=bound.run_token) as sdk:
            yield sdk


@asynccontextmanager
async def connect(url: str, *, run_token: str) -> AsyncIterator[Craft]:
    """Connect with a root-injected credential for an existing owned active CRAFT run.

    This function neither issues nor renews credentials. The root controller
    must revoke them when the run ends. Prefer an injected Session when that
    binding is unavailable. Never pass session cookies or model/database keys.
    """
    target = urlsplit(url)
    if (
        target.scheme not in {"http", "https"}
        or not target.hostname
        or target.username is not None
        or target.password is not None
        or target.query
        or target.fragment
        or not run_token
        or any(value.isspace() for value in run_token)
    ):
        raise ValueError("Expected a configured MCP endpoint and scoped run credential")
    async with httpx.AsyncClient(
        headers={"Authorization": "Bearer " + run_token},
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        trust_env=False,
        follow_redirects=False,
        timeout=120,
    ) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield Craft(session)
