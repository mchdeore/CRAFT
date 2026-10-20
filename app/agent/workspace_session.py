"""Root-side SDK session borrowing one owned run's native transport and middleware.

The run controller owns authentication, connection, revocation, and this adapter's
closure. This prerequisite provides neither a kernel bridge nor standalone login.
"""

import asyncio
import math
from dataclasses import replace
from typing import Any
from uuid import uuid4

from agent_framework import FunctionInvocationContext, FunctionTool
from agent_framework.exceptions import ToolExecutionException
from anyio import ClosedResourceError
from mcp import ClientSession
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ListToolsResult, PaginatedRequestParams

from app.infrastructure.mcp.client import LocalHTTPTool, ToolCallMiddleware
from app.infrastructure.operations.context import CallContext, current_call
from app.infrastructure.operations.registry import Operation, registry, tool_named


class RunBoundSession:
    """Implement the SDK Session protocol without opening or selecting a connection."""

    def __init__(
        self,
        transport: LocalHTTPTool,
        middleware: ToolCallMiddleware,
        *,
        context: CallContext,
        deadline: float,
    ):
        caller = context.caller
        if caller.user_id is None or caller.run_id is None or caller.conversation_id is None:
            raise ValueError("Expected a trusted owned run context")
        if not math.isfinite(deadline):
            raise ValueError("Expected a finite absolute run deadline")
        self._transport, self._middleware = transport, middleware
        self._context, self._deadline = replace(context), deadline
        self._session = transport.session
        self._closed = False
        self._functions = {
            function.name: function
            for function in transport.functions
            if tool_named(registry, function.name) is not None
            and (function.additional_properties or {}).get("_mcp_is_tool")
            and (function.additional_properties or {}).get("_mcp_remote_name") == function.name
        }

    def _check_open(self) -> ClientSession:
        if (
            self._closed
            or not self._transport.is_connected
            or self._session is None
            or self._transport.session is not self._session
        ):
            raise ToolExecutionException("Run-bound native session is closed or disconnected.")
        if asyncio.get_running_loop().time() >= self._deadline:
            raise TimeoutError("Run deadline reached.")
        return self._session

    def _function(self, name: str) -> tuple[FunctionTool, Operation]:
        function = self._functions.get(name)
        declaration = tool_named(registry, name)
        if function is None or function not in self._transport.functions or declaration is None:
            raise ToolExecutionException(f"Native tool '{name}' is not available in this run.")
        return function, declaration

    async def list_tools(self) -> ListToolsResult:
        """Preserve native capability metadata, including gated mutations, for allowed tools."""
        session = self._check_open()
        tools = []
        params = None
        cursors = set()
        async with asyncio.timeout_at(self._deadline):
            while True:
                self._check_open()
                page = await session.list_tools(params=params)
                self._check_open()
                tools.extend(
                    tool
                    for tool in page.tools
                    if tool.name in self._functions
                    and self._functions[tool.name] in self._transport.functions
                    and tool_named(registry, tool.name) is not None
                )
                if not page.nextCursor:
                    break
                if page.nextCursor in cursors:
                    raise ToolExecutionException("Native tool discovery repeated a cursor.")
                cursors.add(page.nextCursor)
                params = PaginatedRequestParams(cursor=page.nextCursor)
        return ListToolsResult(tools=tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        self._check_open()
        function, declaration = self._function(name)
        if declaration.effects != "NONE" or function.approval_mode != "never_require":
            raise ToolExecutionException(
                f"Native tool '{name}' requires the CRAFT approval/resume path; "
                "run-bound SDK dispatch is unavailable for this capability."
            )
        if self._transport._tool_task_support_by_name.get(name) == "required":
            raise ToolExecutionException("Native task capabilities require the run controller.")
        identifier = (meta or {}).get("craft/toolCallId", str(uuid4()))
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 200:
            raise ValueError("Expected a native call ID of 1 to 200 characters")
        invocation = FunctionInvocationContext(
            function,
            function._prepare_arguments(dict(arguments or {})),
            metadata={"call_id": identifier},
        )

        async def dispatch():
            self._check_open()
            invocation.result = await function.invoke(context=invocation, tool_call_id=identifier)
            self._check_open()

        token = current_call.set(replace(self._context))
        try:
            with self._transport.capture_result() as captured:
                try:
                    async with asyncio.timeout_at(self._deadline):
                        await self._middleware.process(invocation, dispatch)
                except ToolExecutionException as error:
                    cause = error.__cause__
                    if isinstance(cause, ClosedResourceError) or (
                        isinstance(cause, McpError)
                        and "session terminated" in cause.error.message.lower()
                    ):
                        self._closed = True
                    if len(captured) != 1 or not captured[0].isError:
                        raise
                    self._check_open()
                    return captured[0]
                if len(captured) != 1:
                    raise ToolExecutionException(
                        "Native tool result was not captured exactly once."
                    )
                self._check_open()
                return captured[0]
        finally:
            current_call.reset(token)

    async def close(self) -> None:
        """Withdraw this SDK capability; the controller still owns the borrowed transport."""
        self._closed = True
