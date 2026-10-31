"""Private per-run MCP transport; the root controller supplies serving and kernels.

Serve this HTTP-only ASGI app on a root-owned 127.0.0.1 ephemeral socket inside
``run()``. This component opens no socket and supplies no standalone login.
"""

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from hmac import compare_digest
from typing import Any

from agent_framework.exceptions import ToolExecutionException
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, CallToolResult, ErrorData, ListToolsRequest, ListToolsResult
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from app.agent.workspace_session import RunBoundSession

_UNAVAILABLE = "Run-bound native capability is unavailable."
_APPROVAL = "This native capability requires the CRAFT approval/resume path."
_HOSTS = ["127.0.0.1", "localhost"]


@dataclass(frozen=True, slots=True)
class _RunBearer:
    _value: str = field(repr=False)

    def __str__(self) -> str:
        return "**********"

    def get_secret_value(self) -> str:
        return self._value


class WorkspaceRunBridge:
    """Borrow exactly one adapter; its bearer and manager cannot be restarted."""

    def __init__(self, session: RunBoundSession, host: str = "127.0.0.1"):
        self._session: RunBoundSession | None = session
        # The Host the kernel connects with: loopback, or this pod's address on the cluster.
        self._hosts = sorted({*_HOSTS, host})
        # The adapter owns this absolute deadline; do not issue a fresh bridge TTL.
        self._deadline = session._deadline
        self._bearer: _RunBearer | None = _RunBearer(secrets.token_urlsafe(32))
        self._started = self._closed = False
        self._ready, self._stop = asyncio.Event(), asyncio.Event()
        self._manager: StreamableHTTPSessionManager | None = None
        self._task: asyncio.Task[None] | None = None
        self._server = self._build_server()

    @property
    def bearer(self) -> _RunBearer | None:
        """Root-only provisioning value, masked by repr/str and withdrawn on close."""
        return self._bearer

    def _borrow(self) -> RunBoundSession:
        session = self._session
        if self._closed or session is None:
            raise RuntimeError(_UNAVAILABLE)
        session._check_open()
        return session

    def _build_server(self) -> Server:
        server = Server("craft-workspace-run")

        @server.list_tools()
        async def list_tools(_request: ListToolsRequest) -> ListToolsResult:
            try:
                return await self._borrow().list_tools()
            except Exception:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=_UNAVAILABLE)) from None

        @server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
            try:
                meta = server.request_context.meta
                return await self._borrow().call_tool(
                    name, arguments, meta=meta.model_dump() if meta is not None else None
                )
            except ToolExecutionException as error:
                if "requires the CRAFT approval/resume path;" in str(error):
                    raise RuntimeError(_APPROVAL) from None
                raise RuntimeError(_UNAVAILABLE) from None
            except Exception:
                raise RuntimeError(_UNAVAILABLE) from None

        return server

    async def _manage(self) -> None:
        manager = StreamableHTTPSessionManager(
            self._server,
            json_response=True,
            stateless=True,
            security_settings=TransportSecuritySettings(
                allowed_hosts=[*self._hosts, *(host + ":*" for host in self._hosts)],
            ),
        )
        try:
            async with manager.run():
                self._manager = manager
                self._ready.set()
                try:
                    async with asyncio.timeout_at(self._deadline):
                        await self._stop.wait()
                except TimeoutError:
                    pass
        finally:
            self._manager = None
            self._ready.set()
            await self._withdraw()

    @asynccontextmanager
    async def run(self) -> AsyncIterator["WorkspaceRunBridge"]:
        """Keep official manager resources in their own task so close joins cleanup."""
        if self._started or self._closed:
            raise RuntimeError("Workspace bridge lifecycle is single-use.")
        self._started = True
        try:
            self._borrow()
            self._task = asyncio.create_task(self._manage(), name="craft.workspace-bridge")
            await self._ready.wait()
            if self._manager is None:
                raise RuntimeError(_UNAVAILABLE)
            yield self
        finally:
            await self.close()

    async def _withdraw(self) -> None:
        self._closed, self._bearer = True, None
        session, self._session = self._session, None
        if session is not None:
            await session.close()

    async def close(self) -> None:
        """Withdraw the bearer/adapter and await termination of per-request resources."""
        await self._withdraw()
        self._stop.set()
        if self._task is not None:
            await asyncio.shield(self._task)

    def _authorized(self, scope: Scope) -> bool:
        if self._manager is None or self._bearer is None or scope.get("query_string"):
            return False
        try:
            self._borrow()
        except Exception:
            return False
        values = Headers(scope=scope).getlist("authorization")
        if len(values) != 1:
            return False
        scheme, _, credential = values[0].partition(" ")
        return scheme.lower() == "bearer" and compare_digest(
            credential.encode(), self._bearer.get_secret_value().encode()
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            raise RuntimeError("Workspace bridge requires an external ASGI lifespan.")
        if not self._authorized(scope):
            await JSONResponse(
                {"error": "Workspace bridge authorization failed."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        if scope["path"] != "/mcp":
            await JSONResponse({"error": "Not found."}, status_code=404)(scope, receive, send)
            return
        manager = self._manager
        started = False

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            if manager is None:
                raise RuntimeError(_UNAVAILABLE)
            async with asyncio.timeout_at(self._deadline):
                await manager.handle_request(scope, receive, guarded_send)
        except Exception:
            if not started:
                await JSONResponse({"error": _UNAVAILABLE}, status_code=503)(scope, receive, send)
