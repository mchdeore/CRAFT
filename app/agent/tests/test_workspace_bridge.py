"""Official in-process MCP HTTP over the reviewed run-bound native adapter."""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError

import httpx
import pytest
from anyio import ClosedResourceError
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import LATEST_PROTOCOL_VERSION, CallToolResult, TextContent

from app.agent.tests.test_workspace_session import bound, native_tools

BASE = "http://127.0.0.1:54321"
HTTP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": LATEST_PROTOCOL_VERSION,
}


def request(method):
    params = {}
    if method == "initialize":
        params = {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "generated-test", "version": "1"},
        }
    elif method == "tools/call":
        params = {
            "name": "search",
            "arguments": {"query": "generated"},
            "_meta": {"craft/toolCallId": "generated-call"},
        }
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


@asynccontextmanager
async def running(**kwargs):
    from app.agent.workspace_bridge import WorkspaceRunBridge

    async with bound(**kwargs) as run:
        bridge = WorkspaceRunBridge(run.session)
        async with bridge.run():
            yield run, bridge


@asynccontextmanager
async def http_client(bridge, *, host="127.0.0.1", authenticated=True):
    headers = dict(HTTP_HEADERS)
    if authenticated:
        assert bridge.bearer is not None
        headers["Authorization"] = "Bearer " + bridge.bearer.get_secret_value()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=bridge),
        base_url=f"http://{host}:54321",
        headers=headers,
        trust_env=False,
        follow_redirects=False,
    ) as http:
        yield http


@asynccontextmanager
async def official_client(bridge, *, host="127.0.0.1"):
    async with http_client(bridge, host=host) as http:
        async with streamable_http_client(str(http.base_url.join("mcp")), http_client=http) as (
            read,
            write,
            session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert session_id() is None
                yield session, http


@pytest.mark.parametrize("method", ["initialize", "tools/list", "tools/call"])
def test_auth_denial_precedes_body_read_and_mcp_transport(method):
    async def execute():
        async with running() as (run, bridge):
            run.remote.list_calls.clear()
            reads = []

            async def app(scope, receive, send):
                async def counted_receive():
                    reads.append(True)
                    return await receive()

                await bridge(scope, counted_receive, send)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=BASE, headers=HTTP_HEADERS
            ) as http:
                missing = await http.post("/mcp", json=request(method))
                wrong = await http.post(
                    "/mcp", json=request(method), headers={"Authorization": "Bearer wrong"}
                )
            assert missing.status_code == wrong.status_code == 401
            assert missing.json() == wrong.json()
            assert missing.headers["WWW-Authenticate"] == "Bearer"
            assert not reads and not run.remote.list_calls and not run.remote.calls
            assert run.budget["calls"] == 0

    asyncio.run(execute())


@pytest.mark.parametrize("failed", [False, True])
def test_official_client_preserves_native_schema_result_meta_and_middleware(failed, caplog):
    caplog.set_level(logging.INFO, logger="craft.operations")
    value = {"hits": [{"text": "Generated owned source"}]}
    reply = CallToolResult(
        content=[TextContent(type="text", text='{"hits":[]}', _meta={"native-content": True})],
        structuredContent=value,
        _meta={"native-result": True, "craft/toolCallId": "native-receipt"},
        isError=failed,
    )

    async def execute():
        async with running(reply=reply, allowed=["search", "write_note"]) as (run, bridge):
            assert bridge.bearer is not None
            secret = bridge.bearer.get_secret_value()
            assert secret not in repr(bridge) and secret not in repr(bridge.bearer)
            assert str(bridge.bearer) == "**********"
            with pytest.raises(FrozenInstanceError):
                bridge.bearer.__setattr__("_value", "generated-replacement")
            assert bridge.bearer.get_secret_value() == secret
            async with official_client(bridge) as (session, _):
                listed = await session.list_tools()
                expected = [
                    tool for tool in native_tools() if tool.name in {"search", "write_note"}
                ]
                assert [tool.model_dump(by_alias=True) for tool in listed.tools] == [
                    tool.model_dump(by_alias=True) for tool in expected
                ]
                received = await session.call_tool(
                    "search", {"query": "generated"}, meta={"craft/toolCallId": "bridge-call"}
                )
            assert received.model_dump(by_alias=True) == reply.model_dump(by_alias=True)
            assert run.evidence == [value] and run.budget["calls"] == 1
            assert len(run.remote.calls) == 1
            name, arguments, meta, active = run.remote.calls[0]
            assert name == "search" and arguments == {"query": "generated"}
            assert meta == {"craft/toolCallId": "bridge-call", "generated-catalog": "native"}
            assert active.caller.user_id == run.parent.caller.user_id
            assert active.caller.run_id == run.parent.caller.run_id
            assert active.caller.tool_call_id == "bridge-call"
            assert run.trace[0]["toolCallId"] == "bridge-call"
            fields = [record.fields for record in caplog.records if hasattr(record, "fields")][-1]
            assert fields["outcome"] == ("error" if failed else "ok")
            assert fields["parent_call_id"] == run.parent.call_id
            assert secret not in caplog.text

    asyncio.run(execute())


def test_gated_mutation_is_advertised_and_refused_before_any_effect():
    async def execute():
        async with running(allowed=["search", "write_note"]) as (run, bridge):
            async with official_client(bridge) as (session, _):
                listed = await session.list_tools()
                mutation = next(tool for tool in listed.tools if tool.name == "write_note")
                assert mutation.annotations is not None
                assert mutation.annotations.readOnlyHint is False
                refused = await session.call_tool("write_note", {"title": "generated"})
                assert isinstance(refused.content[0], TextContent)
                assert refused.isError and "approval/resume" in refused.content[0].text
            assert not run.remote.calls and run.budget["calls"] == 0

    asyncio.run(execute())


def test_explicit_duplicate_requests_each_dispatch_once_and_share_budget():
    async def execute():
        async with running() as (run, bridge):
            run.budget["calls"] = 18
            async with official_client(bridge) as (_, http):
                first = await http.post("/mcp", json=request("tools/call"))
                repeated = await http.post("/mcp", json=request("tools/call"))
                over = await http.post("/mcp", json=request("tools/call"))
            assert not first.json()["result"]["isError"]
            assert not repeated.json()["result"]["isError"]
            assert over.json()["result"]["isError"]
            assert len(run.remote.calls) == 2
            assert run.budget == {"calls": 21, "exceeded": True}
            assert [entry[2] for entry in run.remote.calls] == [
                {"craft/toolCallId": "generated-call", "generated-catalog": "native"},
                {"craft/toolCallId": "generated-call", "generated-catalog": "native"},
            ]

    asyncio.run(execute())


def test_lost_native_response_does_not_resend_or_reconnect():
    async def lost(*_):
        raise ClosedResourceError()

    async def execute():
        async with running(behavior=lost) as (run, bridge):
            async with http_client(bridge) as http:
                first = await http.post("/mcp", json=request("tools/call"))
                second = await http.post("/mcp", json=request("tools/call"))
            assert first.status_code == 200 and first.json()["result"]["isError"]
            assert second.status_code == 401
            assert len(run.remote.calls) == 1 and run.budget["calls"] == 1

    asyncio.run(execute())


def test_bridge_failures_do_not_return_internal_diagnostics(monkeypatch):
    async def unavailable(*_, **__):
        raise RuntimeError("Generated private diagnostic")

    async def execute():
        async with running(behavior=unavailable) as (run, bridge):
            async with official_client(bridge) as (session, _):
                failed = await session.call_tool("search", {"query": "generated"})
                assert failed.isError and isinstance(failed.content[0], TextContent)
                assert failed.content[0].text == "Run-bound native capability is unavailable."
            assert len(run.remote.calls) == 1
        async with running() as (run, bridge):
            monkeypatch.setattr(run.remote, "list_tools", unavailable)
            async with official_client(bridge) as (session, _):
                with pytest.raises(McpError, match="Run-bound native capability is unavailable"):
                    await session.list_tools()

    asyncio.run(execute())


def test_close_cancels_inflight_native_call_and_joins_transport_resources():
    async def execute():
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def waiting(*_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async with running(behavior=waiting) as (run, bridge):
            async with http_client(bridge) as http:
                pending = asyncio.create_task(http.post("/mcp", json=request("tools/call")))
                await asyncio.wait_for(started.wait(), 1)
                await bridge.close()
                response = await asyncio.wait_for(pending, 1)
                assert response.status_code in {500, 503}
                assert (await http.post("/mcp", json=request("tools/call"))).status_code == 401
            assert cancelled.is_set() and len(run.remote.calls) == 1
            assert bridge._manager is None and bridge.bearer is None
            assert bridge._task is not None and bridge._task.done()

    asyncio.run(execute())


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "host.docker.internal"])
def test_only_explicit_local_host_aliases_are_available(host):
    async def execute():
        async with running() as (_, bridge):
            async with official_client(bridge, host=host) as (session, _):
                assert (await session.list_tools()).tools

    asyncio.run(execute())


def test_remote_host_origin_cookies_and_query_never_grant_access():
    async def execute():
        async with running() as (run, bridge):
            run.remote.list_calls.clear()
            async with http_client(bridge) as http:
                remote = await http.post(
                    "/mcp", json=request("initialize"), headers={"Host": "127.0.0.1.evil:54321"}
                )
                origin = await http.post(
                    "/mcp",
                    json=request("initialize"),
                    headers={"Origin": "https://example.invalid"},
                )
                query = await http.post("/mcp?token=generated", json=request("initialize"))
            async with http_client(bridge, authenticated=False) as http:
                cookie = await http.post(
                    "/mcp",
                    json=request("initialize"),
                    headers={"Cookie": "__Host-craft_session=generated"},
                )
                query_only = await http.post("/mcp?token=generated", json=request("initialize"))
            assert remote.status_code == 421 and origin.status_code == 403
            assert query.status_code == cookie.status_code == query_only.status_code == 401
            assert not run.remote.list_calls and not run.remote.calls

    asyncio.run(execute())


def test_expiry_and_close_withdraw_bearer_adapter_and_manager():
    async def execute():
        async with running(remaining=0.06) as (run, bridge):
            async with http_client(bridge) as http:
                await asyncio.sleep(0.1)
                expired = await http.post("/mcp", json=request("initialize"))
                assert expired.status_code == 401
            assert bridge.bearer is None and bridge._manager is None
            assert run.session._closed and bridge._task is not None and bridge._task.done()
        async with running() as (run, bridge):
            async with http_client(bridge) as http:
                await bridge.close()
                closed = await http.post("/mcp", json=request("initialize"))
                assert closed.status_code == 401
            assert bridge.bearer is None and bridge._manager is None
            assert run.session._closed and bridge._task is not None and bridge._task.done()
            with pytest.raises(RuntimeError, match="single-use"):
                async with bridge.run():
                    pass

    asyncio.run(execute())


def test_bearer_cannot_select_a_different_run():
    async def execute():
        async with running() as (first_run, first):
            async with running() as (second_run, second):
                assert first.bearer is not None
                async with http_client(second) as http:
                    rejected = await http.post(
                        "/mcp",
                        json=request("tools/call"),
                        headers={"Authorization": "Bearer " + first.bearer.get_secret_value()},
                    )
                assert rejected.status_code == 401
                assert not first_run.remote.calls and not second_run.remote.calls

    asyncio.run(execute())
