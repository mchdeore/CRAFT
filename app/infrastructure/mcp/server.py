"""Thin MCP adapter: registry, shapes, shared pipeline and encoded results."""

import inspect
from collections.abc import Mapping
from dataclasses import replace
from time import perf_counter
from typing import Any

import msgspec
from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, ListToolsRequest, ListToolsResult, TextContent, Tool
from starlette.concurrency import run_in_threadpool

from app.infrastructure.access import Caller
from app.infrastructure.errors import ApiError, NotFound
from app.infrastructure.operations.context import CallContext, current_call
from app.infrastructure.operations.pipeline import token_allowed
from app.infrastructure.operations.registry import Operation, tool_named, tool_operations
from app.infrastructure.operations.schemas import input_schema as input_schema


def error_result(error: ApiError) -> CallToolResult:
    encoded = {"code": error.code, "message": type(error).message}
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=msgspec.json.encode(encoded).decode())],
        structuredContent=encoded,
    )


def build_server(registry: Mapping[str, Operation], caller) -> Server:
    server = Server("craft")

    async def context():
        if isinstance(caller, Caller):
            return replace(caller, origin="mcp"), None
        return await caller()

    @server.list_tools()
    async def list_tools(request: ListToolsRequest) -> ListToolsResult:
        trusted, _ = await context()
        # A personal token lists only what it may call; the pipeline refuses the rest. A result
        # (not a list) adds to the SDK's tool cache, which every caller shares, instead of
        # replacing it, so a short list never sends a run token's next call to list again.
        token = trusted.workspace_token_id is not None
        return ListToolsResult(
            tools=[
                Tool(
                    name=o.tool_name,
                    description=inspect.getdoc(o.function) or "",
                    inputSchema=input_schema(o),
                )
                for o in tool_operations(registry)
                if not token or token_allowed(o, trusted.origin)
            ]
        )

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        operation = tool_named(registry, name)
        if operation is None:
            return error_result(NotFound())
        try:
            trusted, principal = await context()
            meta = server.request_context.meta
            identifier = meta.model_dump().get("craft/toolCallId") if meta is not None else None
            if identifier is not None and (
                not isinstance(identifier, str) or not 1 <= len(identifier) <= 200
            ):
                from app.infrastructure.errors import InvalidInput

                raise InvalidInput()
            trusted = replace(trusted, tool_call_id=identifier or trusted.tool_call_id)
            token = None
            if principal is not None and principal.parent_call_id is not None:
                token = current_call.set(
                    CallContext(
                        principal.request_id,
                        principal.parent_call_id,
                        None,
                        trusted,
                        perf_counter(),
                    )
                )
            try:
                if inspect.iscoroutinefunction(operation.function):
                    result = await operation.function(trusted, arguments)
                else:
                    result = await run_in_threadpool(operation.function, trusted, arguments)
            finally:
                if token is not None:
                    current_call.reset(token)
            encoded = msgspec.to_builtins(result)
            structured = (
                encoded
                if isinstance(encoded, dict)
                else {"items": encoded}
                if isinstance(encoded, list)
                else {}
            )
            return CallToolResult(
                content=[TextContent(type="text", text=msgspec.json.encode(encoded).decode())],
                structuredContent=structured,
            )
        except ApiError as error:
            return error_result(error)

    return server


class HttpTransport:
    """One registry server with a fresh stateless manager for each app lifespan."""

    def __init__(self, registry):
        from app.infrastructure.mcp import auth

        self.server = build_server(registry, auth.current)
        self.manager: Any = None

    def run(self):
        from contextlib import asynccontextmanager

        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from mcp.server.transport_security import TransportSecuritySettings

        @asynccontextmanager
        async def lifespan():
            manager = StreamableHTTPSessionManager(
                self.server,
                json_response=True,
                stateless=True,
                security_settings=TransportSecuritySettings(
                    allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
                ),
            )
            self.manager = manager
            try:
                async with manager.run():
                    yield
            finally:
                self.manager = None

        return lifespan()

    async def __call__(self, scope, receive, send):
        from starlette.requests import Request

        from app.infrastructure.errors import error_response
        from app.infrastructure.mcp import auth

        request = Request(scope, receive)
        request.state.operation = "mcp"  # read by the traffic middleware
        try:
            entry = await auth.authenticate(request)
        except ApiError as error:
            response = error_response(error)
            if error.status == 401:
                response.headers["WWW-Authenticate"] = "Bearer"
            await response(scope, receive, send)
            return
        request.state.caller = entry.caller
        token = auth.credential.set(entry)
        try:
            if self.manager is None:
                raise RuntimeError("MCP transport lifespan is unavailable")
            await self.manager.handle_request(scope, receive, send)
        finally:
            auth.credential.reset(token)
