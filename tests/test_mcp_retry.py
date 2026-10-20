"""A dispatched MCP call must not be replayed after its response becomes uncertain."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agent_framework.exceptions import ToolExecutionException
from anyio import ClosedResourceError
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ErrorData, TextContent

from app.infrastructure.mcp.client import client


def response(value, *, failed=False, structured=True):
    return CallToolResult(
        isError=failed,
        structuredContent=value if structured else None,
        content=[TextContent(type="text", text=json.dumps(value))],
    )


def tool(results):
    selected = client(
        "http://127.0.0.1:1/mcp",
        "synthetic-token-never-sent",
        results,
        allowed=["write_note"],
    )
    selected._tool_param_names_by_name["write_note"] = {"title", "body"}
    selected.connect = AsyncMock()
    return selected


async def dispose(selected):
    selected.session = None
    await selected._local_http.aclose()


@pytest.mark.parametrize("structured", [True, False])
def test_fresh_success_preserves_checked_arguments_and_call_identity(structured):
    async def run():
        results = []
        selected = tool(results)
        called = AsyncMock(
            return_value=response({"id": "synthetic-note-receipt"}, structured=structured)
        )
        selected.session = SimpleNamespace(call_tool=called)
        try:
            result = await selected.call_tool(
                "write_note",
                title="Generated scratchpad",
                body="12",
                unknown="must-not-be-forwarded",
                _meta={"craft/toolCallId": "synthetic-note-call"},
            )
            assert result == '{"id":"synthetic-note-receipt"}'
            assert results == [{"id": "synthetic-note-receipt"}]
            assert called.await_count == 1
            assert called.call_args.args == ("write_note",)
            assert called.call_args.kwargs["arguments"] == {
                "title": "Generated scratchpad",
                "body": "12",
            }
            assert called.call_args.kwargs["meta"]["craft/toolCallId"] == "synthetic-note-call"
            selected.connect.assert_not_awaited()
        finally:
            await dispose(selected)

    asyncio.run(run())


@pytest.mark.parametrize("lost", ["closed_resource", "session_terminated"])
def test_accepted_call_with_lost_response_is_never_replayed(lost):
    async def run():
        results, attempts, committed = [], [], []
        selected = tool(results)

        async def accept_then_lose(tool_name, *, arguments, meta):
            attempts.append((tool_name, arguments, meta))
            if len(attempts) == 1:
                committed.append("executed")
                if lost == "closed_resource":
                    raise ClosedResourceError()
                raise McpError(ErrorData(code=-32000, message="Session terminated"))
            return response(
                {"code": "approval_required", "message": "Approval required."}, failed=True
            )

        selected.session = SimpleNamespace(call_tool=accept_then_lose)
        try:
            with pytest.raises(ToolExecutionException):
                await selected.call_tool(
                    "write_note",
                    title="Generated scratchpad",
                    body="12",
                    _meta={"craft/toolCallId": "synthetic-note-call"},
                )
            assert committed == ["executed"]
            assert len(attempts) == 1, "A dispatched call with an uncertain outcome was replayed."
            selected.connect.assert_not_awaited()
            assert results == []
        finally:
            await dispose(selected)

    asyncio.run(run())


def test_ordinary_mcp_error_is_not_replayed_or_parsed_as_success():
    async def run():
        results = []
        selected = tool(results)
        called = AsyncMock(
            side_effect=McpError(ErrorData(code=-32602, message="Invalid generated fixture input"))
        )
        selected.session = SimpleNamespace(call_tool=called)
        try:
            with pytest.raises(ToolExecutionException):
                await selected.call_tool("write_note", title="Generated scratchpad", body="12")
            assert called.await_count == 1
            selected.connect.assert_not_awaited()
            assert results == []
        finally:
            await dispose(selected)

    asyncio.run(run())


def test_failed_mcp_result_remains_an_error():
    async def run():
        results = []
        selected = tool(results)
        value = {"code": "approval_required", "message": "Approval required."}
        called = AsyncMock(return_value=response(value, failed=True))
        selected.session = SimpleNamespace(call_tool=called)
        try:
            with pytest.raises(ToolExecutionException):
                await selected.call_tool("write_note", title="Generated scratchpad", body="12")
            assert called.await_count == 1
            selected.connect.assert_not_awaited()
            assert results == [value]
        finally:
            await dispose(selected)

    asyncio.run(run())


def test_cancellation_propagates_without_retry_or_result():
    async def run():
        results = []
        selected = tool(results)
        called = AsyncMock(side_effect=asyncio.CancelledError())
        selected.session = SimpleNamespace(call_tool=called)
        try:
            with pytest.raises(asyncio.CancelledError):
                await selected.call_tool("write_note", title="Generated scratchpad", body="12")
            assert called.await_count == 1
            selected.connect.assert_not_awaited()
            assert results == []
        finally:
            await dispose(selected)

    asyncio.run(run())
