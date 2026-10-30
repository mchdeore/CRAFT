"""Generated workspaces: no Hub, Jupyter server or MCP transport is reached."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from time import perf_counter
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from agent_framework.exceptions import ToolException
from mcp import ClientSession
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

from app.agent import runtime
from app.agent.handlers import RunAgentInput
from app.agent.workspace_session import RunBoundSession
from app.approvals import handlers as approvals
from app.infrastructure import hub, workspaces
from app.infrastructure.access import Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.errors import ApiError
from app.infrastructure.mcp.client import LocalHTTPTool, ToolCallMiddleware, client
from app.infrastructure.operations.context import CallContext, current_call
from app.infrastructure.operations.storage import call_log
from app.notes import handlers as notes
from app.skills.native import SavedSkillsProvider
from app.workspace import handlers as workspace_handlers

HUB = {"CRAFT_HUB_URL": "http://127.0.0.1:1", "CRAFT_HUB_TOKEN": "generated-hub-token"}
KERNEL = "00000000-0000-4000-8000-000000000101"


def result(value, *, failed=False, structured=True):
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(value))],
        structuredContent=value if structured else None,
        isError=failed,
    )


def native_tools():
    return [
        Tool(
            name="search",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            outputSchema={"type": "object"},
            _meta={"generated-catalog": "native"},
        ),
        Tool(
            name="read_skill_version",
            inputSchema={
                "type": "object",
                "properties": {"id": {"type": "string"}, "revision": {"type": "integer"}},
                "required": ["id", "revision"],
            },
        ),
        Tool(
            name=notes.write_note.__operation__.tool_name,
            inputSchema={"type": "object", "properties": {"title": {"type": "string"}}},
            annotations=ToolAnnotations(readOnlyHint=False),
            _meta={"generated-catalog": "gated-mutation"},
        ),
        Tool(name="unknown_tool", inputSchema={"type": "object"}),
    ]


class Remote:
    """Only the network boundary is fake; no server or credentials are used."""

    def __init__(self, reply, behavior=None):
        tools = native_tools()
        self.pages = [
            ListToolsResult(tools=tools[:2], nextCursor="rest"),
            ListToolsResult(tools=tools[2:]),
        ]
        self.reply, self.behavior = reply, behavior
        self.calls, self.list_calls = [], []

    async def list_tools(self, params=None):
        cursor = params.cursor if params else None
        self.list_calls.append(cursor)
        return self.pages[1] if cursor == "rest" else self.pages[0]

    async def call_tool(self, name, *, arguments, meta):
        active = current_call.get()
        self.calls.append(
            (name, dict(arguments), dict(meta or {}), replace(active) if active else None)
        )
        if self.behavior is not None:
            return await self.behavior(name, arguments, meta)
        return self.reply


@asynccontextmanager
async def bound(*, reply=None, behavior=None, allowed=None, remaining=10):
    evidence, trace = [], []
    budget = {"calls": 0, "exceeded": False}
    allowed = allowed if allowed is not None else [tool.name for tool in native_tools()]
    skills = SavedSkillsProvider([], allowed)
    transport = client("http://127.0.0.1:1/mcp", "generated-never-sent", evidence, allowed=allowed)
    remote = Remote(reply if reply is not None else result({"hits": []}), behavior)
    transport.session = cast(ClientSession, remote)
    transport._ping_available = False
    try:
        await transport.load_tools()
        transport.is_connected = True
        caller = Caller(
            user_id=uuid4(), conversation_id=uuid4(), run_id=uuid4(), auth_session_id=uuid4()
        )
        parent = CallContext(uuid4(), uuid4(), None, caller, perf_counter())
        middleware = ToolCallMiddleware(
            trace, budget, 20, skills.completed, external_tools={"workspace_read-file"}
        )
        session = RunBoundSession(
            transport,
            middleware,
            context=parent,
            deadline=asyncio.get_running_loop().time() + remaining,
        )
        yield SimpleNamespace(
            session=session,
            transport=transport,
            remote=remote,
            evidence=evidence,
            trace=trace,
            budget=budget,
            skills=skills,
            middleware=middleware,
            parent=parent,
        )
    finally:
        transport.session = None
        await transport._local_http.aclose()


def generated_hub(monkeypatch, *, ready=True):
    """A generated Hub: records each person it is asked about and never opens a connection."""
    reached = []

    async def state(hub, user_id):
        reached.append(("state", user_id))
        return "stopped"

    async def wait_ready(hub, user_id, seconds=90):
        reached.append(("wait", user_id))
        return ready

    async def kernel(hub, user_id):
        reached.append(("kernel", user_id))
        return KERNEL

    async def start_workspace(caller):
        reached.append(("start", caller.user_id))

    monkeypatch.setattr(workspace_handlers, "start_workspace", start_workspace)
    monkeypatch.setattr(runtime.hub.Hub, "state", state)
    monkeypatch.setattr(runtime.hub.Hub, "wait_ready", wait_ready)
    monkeypatch.setattr(runtime.hub.Hub, "kernel", kernel)
    return reached


async def discover(selected):
    """Use real framework name normalization and allowlist filtering on generated tools."""
    selected._ping_available = False
    selected.session = SimpleNamespace(
        list_tools=AsyncMock(
            return_value=ListToolsResult(
                tools=[
                    Tool(
                        name="read_notebook",
                        inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
                    ),
                    Tool(name="execute code", inputSchema={"type": "object"}),
                    Tool(name="search", inputSchema={"type": "object"}),
                ]
            )
        )
    )
    await selected.load_tools()


def prepared_run(monkeypatch, tmp_path, *, configured="owned", failure=None):
    """Replace only database/model/network boundaries; run the real controller and factory."""
    hub = {} if configured == "none" else HUB
    settings = Settings.from_env({"CRAFT_APP_DB_PASSWORD": "generated-never-sent", **hub})
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
        "inputs": [],
        "finished": [],
        "revoked": [],
        "controllers": [],
    }
    generated_hub(monkeypatch, ready=configured != "not-ready")

    class Controller:
        def __init__(self, session, target):
            recorded["controllers"].append(target)

        async def start(self):
            return ""

        async def close(self):
            recorded["controllers"].append("closed")

    monkeypatch.setattr(runtime, "WorkspaceRunController", Controller)
    monkeypatch.setattr(approvals, "list_notebook_grants", lambda *_: [])
    monkeypatch.setattr(runtime.conversations, "require_owned", lambda *_, **__: None)
    monkeypatch.setattr(
        runtime.conversations, "load_for_run", lambda *_: SimpleNamespace(messages=[])
    )
    monkeypatch.setattr(runtime.conversations, "append_for_run", lambda *_: None)
    monkeypatch.setattr(runtime.service, "start", lambda *_: {})
    monkeypatch.setattr(
        runtime.service,
        "finish",
        lambda *args, **kwargs: recorded["finished"].append((args, kwargs)),
    )
    monkeypatch.setattr(
        runtime, "issue_run_token", lambda *_: SimpleNamespace(token="generated-never-sent")
    )
    monkeypatch.setattr(runtime, "revoke_run_token", lambda *args: recorded["revoked"].append(args))
    original_close = LocalHTTPTool.close

    async def connect(selected):
        recorded["connected"].append(selected)
        if selected.name == "user_workspace" and failure == "connect":
            raise ToolException("generated")  # how the MCP tool reports a failed connection
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
            recorded["inputs"].append(data)
            if failure == "execute":
                raise ApiError()
            if failure == "cancel":
                raise asyncio.CancelledError()
            yield SimpleNamespace(model_dump=lambda **_: {"type": "RUN_FINISHED"})

    monkeypatch.setattr(runtime, "StatefulAgent", PreparedAgent)
    monkeypatch.setattr(runtime, "DurableApprovalAgent", PreparedAdapter)
    return caller, request, recorded


@pytest.fixture
def workspace(monkeypatch, context):
    """A generated owner workspace that records every dispatch instead of reaching Jupyter."""
    calls = []
    files = {}

    async def ready(found, user_id):
        assert user_id == context["caller"].user_id  # only the caller's own server
        return "ready"

    async def kernel(found, user_id):
        return KERNEL

    async def run(target, code, seconds=120):
        calls.append(("run", target.kernel_id, code))
        return {"status": "ok", "outputs": ["2\n"], "error": None, "truncated": False}

    async def write(target, path, content):
        calls.append(("write", path, content))
        files[path] = content
        return {}

    async def read_nb(target, path):
        if path == "outage.ipynb":
            request = httpx.Request("GET", "http://jupyter/" + path)
            raise httpx.HTTPStatusError("down", request=request, response=httpx.Response(503))
        return files.get(path)

    async def write_nb(target, path, notebook):
        calls.append(("notebook", path, len(notebook["cells"])))
        files[path] = notebook

    monkeypatch.setattr(
        hub, "from_settings", lambda _: hub.Hub("http://127.0.0.1:1", "", "generated")
    )
    monkeypatch.setattr(hub.Hub, "state", ready)
    monkeypatch.setattr(hub.Hub, "kernel", kernel)
    monkeypatch.setattr(workspaces, "run_in_kernel", run)
    monkeypatch.setattr(workspaces, "write_text", write)
    monkeypatch.setattr(workspaces, "read_notebook", read_nb)
    monkeypatch.setattr(workspaces, "write_notebook", write_nb)
    yield calls, files
    # Workspace-origin audit rows would block a later downgrade below migration 0015.
    with context["owner"].begin() as connection:
        connection.execute(
            call_log.delete().where(
                call_log.c.origin == "workspace",
                call_log.c.user_id == context["caller"].user_id,
            )
        )
