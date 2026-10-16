"""Native streamable HTTP tools keep credentials separate from model arguments."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any

import httpx
from agent_framework import FunctionMiddleware, MCPStreamableHTTPTool

from app.infrastructure.errors import ApiError
from app.infrastructure.operations import observe
from app.infrastructure.operations.context import current_call
from app.infrastructure.operations.registry import registry, tool_named, tool_operations


@observe("agent.tool")
async def call_observed(call_next):
    await call_next()


class ToolCallMiddleware(FunctionMiddleware):
    def __init__(self, trace, budget, limit):
        self.trace, self.budget, self.limit = trace, budget, limit

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
        if op is None:
            raise ApiError()
        context.kwargs["_meta"] = {"craft/toolCallId": identifier}
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
        approval_mode="never_require",
        load_prompts=False,
        header_provider=lambda _: {"Authorization": "Bearer " + raw_token},
        parse_tool_results=parse,
        request_timeout=120,
    )
