"""Owned external tools share native limits without inheriting native skill behavior."""

import asyncio
import json
from dataclasses import replace
from time import perf_counter
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agent_framework import FunctionInvocationContext, tool
from mcp.types import ListToolsResult, Tool

from app.agent import runtime
from app.agent.handlers import RunAgentInput
from app.agent.workspace import WorkspaceEndpoint, WorkspaceToolsets
from app.infrastructure.access import Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.errors import ApiError
from app.infrastructure.mcp.client import LocalHTTPTool, ToolCallMiddleware
from app.infrastructure.operations.context import CallContext, current_call
from app.infrastructure.operations.registry import registry, tool_named


async def discover(selected):
    """Use real framework name normalization and allowlist filtering on generated tools."""
    selected._ping_available = False
    selected.session = SimpleNamespace(
        list_tools=AsyncMock(
            return_value=ListToolsResult(
                tools=[
                    Tool(
                        name="read file",
                        inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
                    ),
                    Tool(name="execute code", inputSchema={"type": "object"}),
                    Tool(name="search", inputSchema={"type": "object"}),
                ]
            )
        )
    )
    await selected.load_tools()


def operation_context(caller):
    return CallContext(uuid4(), uuid4(), None, caller, perf_counter())


def test_external_allowlist_keeps_correlation_and_private_trace():
    async def execute():
        owner = Caller(user_id=uuid4())
        selected = WorkspaceToolsets(
            {str(owner.user_id): WorkspaceEndpoint("http://127.0.0.1:1/mcp", {}, ("read file",))}
        ).for_caller(owner)
        try:
            await discover(selected)
            assert [function.name for function in selected.functions] == ["workspace_read-file"]
            trace, budget, completed, dispatched = [], {"calls": 0}, [], []
            middleware = ToolCallMiddleware(
                trace,
                budget,
                20,
                completed.append,
                external_tools={function.name for function in selected.functions},
            )
            invocation = FunctionInvocationContext(
                selected.functions[0],
                {"path": "generated-private-code.py"},
                metadata={"call_id": "owned-file-read"},
            )
            parent = operation_context(owner)
            token = current_call.set(parent)

            async def dispatch():
                active = current_call.get()
                assert active is not None
                dispatched.append(active.caller)
                invocation.result = "generated private file content"

            try:
                await middleware.process(invocation, dispatch)
                assert current_call.get() is parent
            finally:
                current_call.reset(token)
            assert dispatched == [replace(owner, tool_call_id="owned-file-read")]
            assert invocation.kwargs == {"_meta": {"craft/toolCallId": "owned-file-read"}}
            assert trace == [{"tool": "workspace_read-file", "toolCallId": "owned-file-read"}]
            assert not completed and budget["calls"] == 1
        finally:
            await selected.close()

    asyncio.run(execute())


@pytest.mark.parametrize(
    "name,external_tools",
    [
        ("unknown_tool", ()),
        ("workspace_execute-code", ()),
        ("workspace_read-file", ()),
        ("workspace_execute-code", ("workspace_read-file",)),
        ("other_read-file", ("workspace_read-file",)),
    ],
)
def test_unknown_or_unconfigured_prefix_never_dispatches(name, external_tools):
    @tool(name=name)
    def generated(path: str) -> str:
        return path

    async def execute():
        invocation = FunctionInvocationContext(generated, {"path": "x"}, metadata={"call_id": "x"})
        trace, dispatched = [], []
        middleware = ToolCallMiddleware(trace, {"calls": 0}, 20, external_tools=external_tools)

        async def dispatch():
            dispatched.append(True)

        with pytest.raises(ApiError):
            await middleware.process(invocation, dispatch)
        assert not dispatched and not trace

    asyncio.run(execute())


def test_native_and_external_calls_share_limit_and_native_completion():
    @tool(name="search")
    def native(query: str) -> str:
        return query

    @tool(name="workspace_read-file")
    def external(path: str) -> str:
        return path

    async def execute():
        trace, completed, dispatched = [], [], []
        budget = {"calls": 18, "exceeded": False}
        middleware = ToolCallMiddleware(
            trace, budget, 20, completed.append, external_tools={external.name}
        )
        parent = operation_context(Caller(user_id=uuid4()))
        token = current_call.set(parent)

        async def dispatch():
            dispatched.append(True)

        try:
            for function, arguments in [
                (native, {"query": "generated"}),
                (external, {"path": "x"}),
            ]:
                invocation = FunctionInvocationContext(
                    function, arguments, metadata={"call_id": function.name}
                )
                await middleware.process(invocation, dispatch)
            with pytest.raises(runtime.RunLimit):
                await middleware.process(invocation, dispatch)
            assert current_call.get() is parent
        finally:
            current_call.reset(token)
        assert len(dispatched) == 2 and budget == {"calls": 21, "exceeded": True}
        assert len(completed) == 1 and completed[0].function.name == "search"
        declaration = tool_named(registry, "search")
        assert declaration is not None
        assert trace[0]["operation"] == declaration.name
        assert trace[0]["arguments"] == {"query": "generated"}
        assert trace[1] == {"tool": "workspace_read-file", "toolCallId": "workspace_read-file"}

    asyncio.run(execute())


def prepared_run(monkeypatch, tmp_path, *, configured="owned", failure=None):
    """Replace only database/model/network boundaries; run the real controller and factory."""
    settings = Settings.from_env({"CRAFT_APP_DB_PASSWORD": "generated-never-sent"})
    caller = Caller(user_id=uuid4(), settings=settings)
    request = RunAgentInput(
        thread_id=uuid4(),
        run_id=uuid4(),
        messages=[{"role": "user", "content": "Use my workspace"}],
    )
    recorded: dict[str, Any] = {
        "connected": [],
        "closed": [],
        "agents": [],
        "finished": [],
        "revoked": [],
    }
    if configured == "none":
        monkeypatch.delenv("USER_WORKSPACES_FILE", raising=False)
    elif configured == "invalid":
        path = tmp_path / "invalid.json"
        path.write_text('{"invalid":')
        monkeypatch.setenv("USER_WORKSPACES_FILE", str(path))
    else:
        owner = caller.user_id if configured == "owned" else uuid4()
        path = tmp_path / "private-workspaces.json"
        path.write_text(
            json.dumps(
                {
                    str(owner): {
                        "url": "http://127.0.0.1:1/mcp",
                        "headers": {},
                        "tools": ["read file"],
                    }
                }
            )
        )
        monkeypatch.setenv("USER_WORKSPACES_FILE", str(path))
    monkeypatch.setattr(
        runtime.conversations, "load_for_run", lambda *_: SimpleNamespace(messages=[])
    )
    monkeypatch.setattr(runtime.conversations, "append_for_run", lambda *_: None)
    monkeypatch.setattr(runtime.service, "start", lambda *_: {})
    monkeypatch.setattr(runtime.service, "finish", lambda *args: recorded["finished"].append(args))
    monkeypatch.setattr(
        runtime, "issue_run_token", lambda *_: SimpleNamespace(token="generated-never-sent")
    )
    monkeypatch.setattr(runtime, "revoke_run_token", lambda *args: recorded["revoked"].append(args))
    original_close = LocalHTTPTool.close

    async def connect(selected):
        recorded["connected"].append(selected)
        if selected.name == "user_workspace" and failure == "connect":
            raise ApiError()
        if selected.name == "user_workspace" and failure == "timeout":
            await asyncio.Event().wait()
        await discover(selected)

    async def close(selected):
        try:
            await original_close(selected)
        finally:
            recorded["closed"].append(selected)

    monkeypatch.setattr(LocalHTTPTool, "connect", connect)
    monkeypatch.setattr(LocalHTTPTool, "close", close)

    class SelectedModel:
        closed = False

        async def close(self):
            self.closed = True

    selected_model = SelectedModel()
    recorded["model"] = selected_model

    def select_model(_):
        if failure == "model":
            raise ApiError()
        return selected_model

    monkeypatch.setattr(runtime.model, "client", select_model)

    class PreparedAgent:
        def __init__(self, *args, saved, **kwargs):
            self.saved, self.options = saved, kwargs
            recorded["agents"].append(self)

    class PreparedAdapter:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, data):
            if failure == "execute":
                raise ApiError()
            if failure == "cancel":
                raise asyncio.CancelledError()
            yield SimpleNamespace(model_dump=lambda **_: {"type": "RUN_FINISHED"})

    monkeypatch.setattr(runtime, "StatefulAgent", PreparedAgent)
    monkeypatch.setattr(runtime, "DurableApprovalAgent", PreparedAdapter)
    return caller, request, recorded


@pytest.mark.parametrize("failure", [None, "connect", "model", "execute", "timeout", "cancel"])
def test_owned_workspace_lifecycle_is_closed_on_all_run_paths(monkeypatch, tmp_path, failure):
    caller, request, recorded = prepared_run(monkeypatch, tmp_path, failure=failure)
    if failure == "timeout":
        monkeypatch.setattr(runtime, "RUN_SECONDS", 0.25)

    async def execute():
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                return [event async for event in runtime._run(caller, request)]
            return []
        return [event async for event in runtime._run(caller, request)]

    events = asyncio.run(execute())
    assert [selected.name for selected in recorded["connected"]] == ["craft", "user_workspace"]
    assert [selected.name for selected in recorded["closed"]] == ["user_workspace", "craft"]
    assert all(selected._local_http.is_closed for selected in recorded["closed"])
    assert len(recorded["finished"]) == len(recorded["revoked"]) == 1
    assert recorded["finished"][0][2] == (
        "finished" if failure is None else "cancelled" if failure == "cancel" else "failed"
    )
    if failure is None:
        options = recorded["agents"][0].options
        assert [selected.name for selected in options["tools"]] == ["craft", "user_workspace"]
        assert options["middleware"][0].external_tools == frozenset({"workspace_read-file"})
        assert "USER_ROOT=/workspace" in options["instructions"]
        assert events[-1].type == "RUN_FINISHED"
    if failure == "timeout":
        assert events[-1].code == "run_timeout"


@pytest.mark.parametrize(
    "configured,read_only", [("none", False), ("other", False), ("invalid", True)]
)
def test_unconfigured_owner_and_read_only_query_keep_native_tools(
    monkeypatch, tmp_path, configured, read_only
):
    caller, request, recorded = prepared_run(monkeypatch, tmp_path, configured=configured)

    async def execute():
        if read_only:
            with runtime.read_only_query():
                return [event async for event in runtime._run(caller, request)]
        return [event async for event in runtime._run(caller, request)]

    events = asyncio.run(execute())
    options = recorded["agents"][0].options
    assert [selected.name for selected in options["tools"]] == ["craft"]
    assert [selected.name for selected in recorded["closed"]] == ["craft"]
    if configured == "other":
        assert "unavailable" in options["instructions"].lower()
    else:
        assert options["instructions"] == (runtime.ROOT / "app/agent/instructions.md").read_text()
    assert events[-1].type == "RUN_FINISHED"


def test_private_workspace_env_is_outside_public_settings_schema():
    settings = Settings.from_env(
        {
            "CRAFT_APP_DB_PASSWORD": "generated-never-sent",
            "USER_WORKSPACES_FILE": "/generated/private-workspaces.json",
        }
    )
    assert settings.model_main == "fake"
