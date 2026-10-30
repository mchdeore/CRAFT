"""Run-bound SDK calls reuse real native transport, middleware, and framework types."""

import asyncio
import hashlib
import logging
from dataclasses import replace
from time import perf_counter
from uuid import uuid4

import msgspec
import pytest
from agent_framework import FunctionInvocationContext, tool
from agent_framework.exceptions import ToolExecutionException
from anyio import ClosedResourceError
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from app.agent import runtime
from app.infrastructure.access import Caller
from app.infrastructure.operations.context import CallContext, current_call
from app.library.handlers import LibraryHits
from app.skills.handlers import Skill
from app.tests.support.workspace import Remote, bound, result


@pytest.mark.parametrize("structured", [True, False])
def test_sdk_retains_native_result_evidence_and_bound_audit_identity(caplog, structured):
    from craft import Craft

    caplog.set_level(logging.INFO, logger="craft.operations")
    quote = "Generated source evidence"
    handle = {
        "item": "itm_0123456789ab",
        "version": 1,
        "block": 1,
        "locator": "p:1",
        "quoteSha256": hashlib.sha256(quote.encode()).hexdigest(),
        "simulated": True,
    }
    value = msgspec.to_builtins(
        msgspec.convert(
            {
                "hits": [
                    {
                        "kind": "chunk",
                        "handle": handle,
                        "title": "Generated source document",
                        "headingPath": "Generated evidence",
                        "text": quote,
                        "score": 1.0,
                        "tags": {"mission": None, "silo": None, "stage": None},
                        "classification": "protected-a",
                        "before": None,
                        "after": None,
                    }
                ],
                "truncated": False,
            },
            LibraryHits,
        )
    )
    reply = result(value, structured=structured)

    async def execute():
        async with bound(reply=reply) as run:
            foreign = CallContext(uuid4(), uuid4(), None, Caller(user_id=uuid4()), perf_counter())
            token = current_call.set(foreign)
            try:
                received = await Craft(run.session).search("generated source")
                assert current_call.get() is foreign
            finally:
                current_call.reset(token)
            assert received is reply and run.evidence == [value]
            (citation,) = runtime.citations(run.evidence, quote)
            assert citation["title"] == value["hits"][0]["title"]
            assert citation["handle"] == handle
            assert citation["quote"] == quote
            name, arguments, meta, active = run.remote.calls[0]
            assert name == "search" and arguments == {"query": "generated source"}
            assert active.caller == replace(
                run.parent.caller, tool_call_id=meta["craft/toolCallId"]
            )
            assert active.request_id == run.parent.request_id
            assert active.parent_call_id == run.parent.call_id
            assert run.trace[0]["toolCallId"] == meta["craft/toolCallId"]
            assert run.trace[0]["arguments"] == arguments and run.budget["calls"] == 1
            fields = [record.fields for record in caplog.records if hasattr(record, "fields")][-1]
            assert fields["name"] == "agent.tool" and fields["outcome"] == "ok"
            for field in ("user_id", "run_id", "conversation_id", "auth_session_id"):
                assert fields[field] == getattr(run.parent.caller, field)
            assert fields["request_id"] == run.parent.request_id
            assert fields["parent_call_id"] == run.parent.call_id

    asyncio.run(execute())


def test_discovery_preserves_current_native_capabilities_and_filters_unknown_tools():
    async def execute():
        async with bound(allowed=["search", "write_note", "unknown_tool"]) as run:
            listed = await run.session.list_tools()
            assert [tool.name for tool in listed.tools] == ["search", "write_note"]
            assert listed.nextCursor is None
            assert listed.tools[0].outputSchema == {"type": "object"}
            assert listed.tools[0].meta == {"generated-catalog": "native"}
            assert listed.tools[1].annotations.readOnlyHint is False
            assert listed.tools[1].meta == {"generated-catalog": "gated-mutation"}
            assert run.remote.list_calls[-2:] == [None, "rest"]
            assert not run.remote.calls and run.budget["calls"] == 0

    asyncio.run(execute())


@pytest.mark.parametrize("name", ["write_note", "unknown_tool", "read_skill_version"])
def test_gated_unknown_and_run_excluded_tools_never_dispatch(name):
    async def execute():
        async with bound(allowed=["search", "write_note", "unknown_tool"]) as run:
            message = "approval/resume" if name == "write_note" else "not available"
            with pytest.raises(ToolExecutionException, match=message):
                await run.session.call_tool(name, {"title": "generated"})
            assert not run.remote.calls and run.budget["calls"] == 0

    asyncio.run(execute())


def test_sdk_and_external_invocations_share_the_same_twenty_call_budget():
    @tool(name="workspace_read-file")
    def external(path: str) -> str:
        return path

    async def execute():
        async with bound() as run:
            run.budget["calls"] = 18
            invocation = FunctionInvocationContext(
                external, {"path": "owned.py"}, metadata={"call_id": "external-call"}
            )

            async def dispatch():
                invocation.result = "owned content"

            token = current_call.set(run.parent)
            try:
                await run.middleware.process(invocation, dispatch)
            finally:
                current_call.reset(token)
            await run.session.call_tool("search", {"query": "generated"})
            with pytest.raises(runtime.RunLimit):
                await run.session.call_tool("search", {"query": "over budget"})
            assert len(run.remote.calls) == 1
            assert run.budget == {"calls": 21, "exceeded": True}
            assert [step["tool"] for step in run.trace] == ["workspace_read-file", "search"]

    asyncio.run(execute())


def test_skill_completion_consumes_existing_native_parsed_result():
    definition = Skill(
        id=uuid4(),
        revision=1,
        name="Generated owned skill",
        description="Generated fixture",
        instructions="Read a generated source.",
        example_inputs=["generated"],
        tool_names=["search"],
        unavailable_tools=[],
        created_at="2026-10-20T00:00:00Z",
        updated_at="2026-10-20T00:00:00Z",
    )
    reply = result(msgspec.to_builtins(definition))

    async def execute():
        async with bound(reply=reply) as run:
            received = await run.session.call_tool(
                "read_skill_version", {"id": str(definition.id), "revision": 1}
            )
            assert received is reply
            assert run.skills.references == [{"id": str(definition.id), "revision": 1}]
            assert run.evidence == [msgspec.to_builtins(definition)]

    asyncio.run(execute())


def test_native_error_result_returns_unchanged_after_error_observation(caplog):
    caplog.set_level(logging.INFO, logger="craft.operations")
    reply = result({"code": "not_found", "message": "Generated error"}, failed=True)

    async def execute():
        async with bound(reply=reply) as run:
            received = await run.session.call_tool("search", {"query": "generated"})
            assert received is reply and received.isError
            assert len(run.remote.calls) == 1 and run.evidence == [reply.structuredContent]
            fields = [record.fields for record in caplog.records if hasattr(record, "fields")][-1]
            assert fields["name"] == "agent.tool" and fields["outcome"] == "error"
            assert current_call.get() is None

    asyncio.run(execute())


@pytest.mark.parametrize("failure", [ClosedResourceError, asyncio.CancelledError])
def test_lost_or_cancelled_native_dispatch_is_not_replayed(failure):
    async def fail(*_):
        raise failure()

    async def execute():
        async with bound(behavior=fail) as run:
            expected = (
                asyncio.CancelledError
                if failure is asyncio.CancelledError
                else ToolExecutionException
            )
            with pytest.raises(expected):
                await run.session.call_tool("search", {"query": "generated"})
            assert len(run.remote.calls) == 1 and not run.evidence
            assert current_call.get() is None

    asyncio.run(execute())


@pytest.mark.parametrize("lost", ["closed", "terminated"])
def test_connection_loss_withdraws_adapter_without_reconnection(lost):
    async def lose(*_):
        if lost == "closed":
            raise ClosedResourceError()
        raise McpError(ErrorData(code=-32000, message="Session terminated"))

    async def execute():
        async with bound(behavior=lose) as run:
            with pytest.raises(ToolExecutionException):
                await run.session.call_tool("search", {"query": "generated"})
            with pytest.raises(ToolExecutionException, match="closed or disconnected"):
                await run.session.call_tool("search", {"query": "generated"})
            with pytest.raises(ToolExecutionException, match="closed or disconnected"):
                await run.session.list_tools()
            assert len(run.remote.calls) == 1

    asyncio.run(execute())


@pytest.mark.parametrize("state", ["closed", "disconnected", "removed", "replaced"])
def test_closed_or_disconnected_adapter_cannot_call_or_discover(state):
    async def execute():
        async with bound() as run:
            listed_before = len(run.remote.list_calls)
            if state == "closed":
                await run.session.close()
                await run.session.close()
                assert run.transport.is_connected
            elif state == "disconnected":
                run.transport.is_connected = False
            elif state == "removed":
                run.transport.session = None
            else:
                run.transport.session = Remote(result({"hits": []}))
            with pytest.raises(ToolExecutionException, match="closed or disconnected"):
                await run.session.call_tool("search", {"query": "generated"})
            with pytest.raises(ToolExecutionException, match="closed or disconnected"):
                await run.session.list_tools()
            assert not run.remote.calls and len(run.remote.list_calls) == listed_before

    asyncio.run(execute())


def test_expired_absolute_deadline_cannot_call_or_discover():
    async def execute():
        async with bound(remaining=-1) as run:
            listed_before = len(run.remote.list_calls)
            with pytest.raises(TimeoutError):
                await run.session.call_tool("search", {"query": "generated"})
            with pytest.raises(TimeoutError):
                await run.session.list_tools()
            assert not run.remote.calls and len(run.remote.list_calls) == listed_before

    asyncio.run(execute())


def test_inflight_native_dispatch_uses_shared_absolute_deadline():
    cancelled = []

    async def wait(*_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def execute():
        async with bound(behavior=wait, remaining=0.05) as run:
            with pytest.raises(TimeoutError):
                await run.session.call_tool("search", {"query": "generated"})
            assert len(run.remote.calls) == 1 and cancelled == [True]
            assert current_call.get() is None

    asyncio.run(execute())


def test_concurrent_calls_keep_distinct_native_results_and_call_identity():
    replies = {query: result({"query": query}) for query in ("one", "two")}

    async def dispatch(_, arguments, __):
        await asyncio.sleep(0)
        return replies[arguments["query"]]

    async def execute():
        async with bound(behavior=dispatch) as run:
            received = await asyncio.gather(
                *[
                    run.session.call_tool(
                        "search", {"query": query}, meta={"craft/toolCallId": query}
                    )
                    for query in ("one", "two")
                ]
            )
            assert received[0] is replies["one"] and received[1] is replies["two"]
            assert run.budget["calls"] == 2 and len(run.remote.calls) == 2
            for _, arguments, meta, active in run.remote.calls:
                assert arguments["query"] == meta["craft/toolCallId"] == active.caller.tool_call_id

    asyncio.run(execute())


@pytest.mark.parametrize("call_id", ["", 1, "x" * 201])
def test_invalid_native_call_identity_fails_before_dispatch(call_id):
    async def execute():
        async with bound() as run:
            with pytest.raises(ValueError, match="call ID"):
                await run.session.call_tool(
                    "search", {"query": "generated"}, meta={"craft/toolCallId": call_id}
                )
            assert not run.remote.calls

    asyncio.run(execute())


def test_constructor_requires_an_owned_run_context():
    from app.agent.workspace_session import RunBoundSession

    async def execute():
        async with bound() as run:
            anonymous = replace(run.parent, caller=Caller())
            with pytest.raises(ValueError, match="owned run"):
                RunBoundSession(
                    run.transport,
                    run.middleware,
                    context=anonymous,
                    deadline=asyncio.get_running_loop().time() + 10,
                )

    asyncio.run(execute())
