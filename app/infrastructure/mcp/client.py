"""Native streamable HTTP tools keep credentials separate from model arguments."""

import json
from dataclasses import replace
from datetime import UTC, datetime

from agent_framework import FunctionMiddleware, MCPStreamableHTTPTool

from app.infrastructure.errors import ApiError
from app.infrastructure.operations import observe
from app.infrastructure.operations.context import current_call
from app.infrastructure.operations.registry import registry


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
        op = next((o for o in registry.values() if o.tool_name == context.function.name), None)
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


def client(url, raw_token, results, allowed=None):
    allowlist = (
        allowed
        if allowed is not None
        else [o.tool_name for o in registry.values() if o.tool and o.effects == "NONE" and o.tool_name != "ask_agent"]
    )

    def parse(result):
        value = result.structuredContent
        if value is None:
            value = next((json.loads(c.text) for c in result.content if c.type == "text"), None)
        results.append(value)
        return json.dumps(value, separators=(",", ":"))

    return MCPStreamableHTTPTool(
        name="craft",
        url=url,
        allowed_tools=allowlist,
        approval_mode="never_require",
        load_prompts=False,
        header_provider=lambda _: {"Authorization": "Bearer " + raw_token},
        parse_tool_results=parse,
        request_timeout=120,
    )
