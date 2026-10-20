"""Native streamable HTTP tools keep credentials separate from model arguments."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any

import httpx
from agent_framework import FunctionMiddleware, MCPStreamableHTTPTool
from agent_framework._mcp import _capture_mcp_tool_result
from agent_framework.exceptions import ToolExecutionException
from agent_framework.observability import set_mcp_span_error
from anyio import ClosedResourceError
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult

from app.infrastructure.errors import ApiError
from app.infrastructure.operations import observe
from app.infrastructure.operations.context import current_call
from app.infrastructure.operations.registry import registry, tool_named, tool_operations


@observe("agent.tool")
async def call_observed(call_next):
    await call_next()


class ToolCallMiddleware(FunctionMiddleware):
    def __init__(self, trace, budget, limit, on_completed=None, *, external_tools=()):
        self.trace, self.budget, self.limit = trace, budget, limit
        self.on_completed = on_completed
        self.external_tools = frozenset(external_tools)

    async def process(self, context, call_next):
        self.budget["calls"] += 1
        if self.budget["calls"] > self.limit:
            self.budget["exceeded"] = True
            from app.agent.runtime import RunLimit

            raise RunLimit()
        identifier = context.metadata.get("call_id")
        if not isinstance(identifier, str) or not identifier:
            raise ApiError()
        op = tool_named(registry, context.function.name)
        if op is None and context.function.name not in self.external_tools:
            raise ApiError()
        context.kwargs["_meta"] = {"craft/toolCallId": identifier}
        if op is None:
            self.trace.append({"tool": context.function.name, "toolCallId": identifier})
        else:
            self.trace.append(
                {
                    "tool": op.tool_name,
                    "operation": op.name,
                    "arguments": dict(context.arguments),
                    "at": datetime.now(UTC).isoformat(),
                    "toolCallId": identifier,
                }
            )
        parent = current_call.get()
        if parent is None:
            raise ApiError()
        token = current_call.set(
            replace(parent, caller=replace(parent.caller, tool_call_id=identifier))
        )
        try:
            await call_observed(call_next)
            if op is not None and self.on_completed is not None:
                self.on_completed(context)
        finally:
            current_call.reset(token)


class LocalHTTPTool(MCPStreamableHTTPTool):
    """Own a direct local transport for one run, without proxy or cookie inheritance."""

    def __init__(self, **kwargs: Any):
        transport = httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=120,
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        )
        super().__init__(http_client=transport, **kwargs)
        self._local_http = transport
        self._result_capture: ContextVar[list[CallToolResult] | None] = ContextVar(
            "native_tool_result_capture", default=None
        )

    @contextmanager
    def capture_result(self) -> Iterator[list[CallToolResult]]:
        """Retain genuine results for a root-side caller without changing native parsing."""
        results: list[CallToolResult] = []
        token = self._result_capture.set(results)
        try:
            yield results
        finally:
            self._result_capture.reset(token)

    async def _call_tool_with_retries(self, tool_name, filtered_kwargs, meta, parser, span):
        """Dispatch once: a lost response does not establish whether the tool ran."""
        try:
            session = self.session
            if session is None:
                raise ToolExecutionException("MCP server is not connected.")
            result = await session.call_tool(tool_name, arguments=filtered_kwargs, meta=meta)
            captured = self._result_capture.get()
            if captured is not None:
                captured.append(result)
            _capture_mcp_tool_result(result)
            parsed = parser(result)
            if result.isError:
                text = (
                    "\n".join(c.text for c in parsed if c.type == "text" and c.text)
                    if isinstance(parsed, list)
                    else str(parsed)
                )
                if span.is_recording():
                    set_mcp_span_error(span, "tool_error")
                raise ToolExecutionException(text or str(parsed))
            return parsed
        except ToolExecutionException:
            raise
        except (ClosedResourceError, McpError) as ex:
            message = ex.error.message if isinstance(ex, McpError) else str(ex)
            if isinstance(ex, ClosedResourceError) or "session terminated" in message.lower():
                message = f"Failed to call tool '{tool_name}' - connection lost; outcome unknown."
            if span.is_recording():
                set_mcp_span_error(span, type(ex).__name__)
            raise ToolExecutionException(message, inner_exception=ex) from ex
        except Exception as ex:
            if span.is_recording():
                set_mcp_span_error(span, type(ex).__name__)
            raise ToolExecutionException(
                f"Failed to call tool '{tool_name}'.", inner_exception=ex
            ) from ex

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            await self._local_http.aclose()


def client(url, raw_token, results, allowed=None):
    allowlist = (
        allowed
        if allowed is not None
        else [o.tool_name for o in tool_operations(registry, agent=True)]
    )

    def parse(result):
        value = result.structuredContent
        if value is None:
            value = next((json.loads(c.text) for c in result.content if c.type == "text"), None)
        results.append(value)
        return json.dumps(value, separators=(",", ":"))

    return LocalHTTPTool(
        name="craft",
        url=url,
        allowed_tools=allowlist,
        approval_mode={
            "always_require_approval": [
                o.tool_name for o in tool_operations(registry) if o.effects != "NONE"
            ],
            "never_require_approval": [
                o.tool_name for o in tool_operations(registry) if o.effects == "NONE"
            ],
        },
        load_prompts=False,
        header_provider=lambda _: {"Authorization": "Bearer " + raw_token},
        parse_tool_results=parse,
        request_timeout=120,
    )
