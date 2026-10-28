"""Root-owned lifecycle, kernel provisioning and shared native execution limits."""

import asyncio
import importlib
import inspect
import json
import logging
import signal
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from agent_framework import FunctionInvocationContext, tool
from agent_framework.exceptions import ToolExecutionException
from mcp.types import CallToolResult, ListToolsResult, Tool

from app.agent.tests.test_workspace_agent import prepared_run
from app.agent.tests.test_workspace_session import bound
from app.infrastructure.access import Caller
from app.infrastructure.operations.context import current_call

KERNEL = "00000000-0000-4000-8000-000000000001"
NOTEBOOK = "notebooks/analysis.ipynb"


def lifecycle_module():
    assert importlib.util.find_spec("app.agent.workspace_run"), "The run lifecycle is missing"
    return importlib.import_module("app.agent.workspace_run")


def test_native_accessor_requires_live_memory_binding_and_never_replaces_it(monkeypatch):
    from craft import client as sdk

    assert hasattr(sdk, "native"), "The no-argument run-bound SDK accessor is missing"
    opened = []

    @asynccontextmanager
    async def connect(url, *, run_token):
        opened.append((url, run_token))
        yield "authenticated-native-sdk"

    monkeypatch.setattr(sdk, "connect", connect)

    async def execute():
        with pytest.raises(RuntimeError, match="active.*binding"):
            async with sdk.native():
                pass
        run_id, foreign = str(uuid4()), str(uuid4())
        try:
            sdk._bind_run("http://127.0.0.1:12345/mcp", "generated-ephemeral", run_id, 30)
            with pytest.raises(RuntimeError, match="already bound"):
                sdk._bind_run("http://127.0.0.1:12346/mcp", "other-ephemeral", foreign, 30)
            assert not sdk._unbind_run(foreign)
            assert "generated-ephemeral" not in repr(sdk._binding)
            async with sdk.native() as client:
                assert client == "authenticated-native-sdk"
            assert opened == [("http://127.0.0.1:12345/mcp", "generated-ephemeral")]
            assert sdk._unbind_run(run_id)
            with pytest.raises(RuntimeError, match="active.*binding"):
                async with sdk.native():
                    pass
        finally:
            sdk._unbind_run(run_id)

    asyncio.run(execute())


def test_workspace_kernel_selection_is_pinned_before_external_dispatch():
    lifecycle = lifecycle_module()

    @tool(name="workspace_execute_code")
    def execute_code(code: str, kernel_id: str | None = None) -> str:
        return code

    @tool(name="workspace_use_notebook")
    def use_notebook(notebook_name: str, notebook_path: str, kernel_id: str | None = None) -> str:
        return notebook_path

    execute_code.additional_properties = {"_mcp_remote_name": "execute_code"}
    use_notebook.additional_properties = {"_mcp_remote_name": "use_notebook"}

    async def execute():
        async with bound() as run:
            trace, budget, dispatched = [], {"calls": 0, "exceeded": False}, []
            middleware = lifecycle.WorkspaceToolCallMiddleware(
                trace,
                budget,
                20,
                external_tools={execute_code.name, use_notebook.name},
                kernel_id=KERNEL,
            )
            middleware.pin_notebook(NOTEBOOK)
            token = current_call.set(run.parent)
            try:
                for function, arguments, allowed in [
                    (execute_code, {"code": "print(1)"}, True),
                    (execute_code, {"code": "print(1)", "kernel_id": "other"}, False),
                    (
                        use_notebook,
                        {"notebook_name": "analysis", "notebook_path": NOTEBOOK},
                        True,
                    ),
                    (
                        use_notebook,
                        {"notebook_name": "other", "notebook_path": "other.ipynb"},
                        False,
                    ),
                ]:
                    invocation = FunctionInvocationContext(
                        function, arguments, metadata={"call_id": str(uuid4())}
                    )

                    async def dispatch(invocation=invocation):
                        dispatched.append(dict(invocation.arguments))

                    if allowed:
                        await middleware.process(invocation, dispatch)
                    else:
                        with pytest.raises(ToolExecutionException, match="pinned"):
                            await middleware.process(invocation, dispatch)
            finally:
                current_call.reset(token)
            assert dispatched == [
                {"code": "print(1)", "kernel_id": KERNEL},
                {"notebook_name": "analysis", "notebook_path": NOTEBOOK, "kernel_id": KERNEL},
            ]
            assert budget["calls"] == 4 and len(trace) == 4
            assert all("arguments" not in step for step in trace)

    asyncio.run(execute())


def test_configured_direct_code_tool_is_withheld_from_the_agent():
    """Code runs go through the approval-gated run_code operation, never directly."""
    from app.agent.workspace import WorkspaceEndpoint, WorkspaceToolsets

    calls = []

    class Remote:
        async def list_tools(self, params=None):
            return ListToolsResult(
                tools=[
                    Tool(name="execute_code", inputSchema={"type": "object"}),
                    Tool(name="read_notebook", inputSchema={"type": "object"}),
                ]
            )

        async def call_tool(self, name, *, arguments, meta):
            calls.append(name)
            return CallToolResult(content=[], structuredContent={})

    async def execute():
        owner = uuid4()
        transport = WorkspaceToolsets(
            {
                str(owner): WorkspaceEndpoint(
                    "http://127.0.0.1:1/mcp", {}, ("execute_code", "read_notebook")
                )
            }
        ).for_caller(Caller(user_id=owner))
        transport.session, transport._ping_available = Remote(), False
        try:
            await transport.load_tools()
            assert [function.name for function in transport.functions] == [
                "workspace_read_notebook"
            ]
        finally:
            transport.session = None
            await transport.close()

    asyncio.run(execute())
    assert calls == []


def test_jupyter_target_rejects_credentials_in_urls_and_arbitrary_bridge_hosts():
    lifecycle = lifecycle_module()
    for url, bridge_host in [
        ("http://127.0.0.1:9998/?token=generated", "127.0.0.1"),
        ("http://127.0.0.1:9998/", "other.invalid"),
    ]:
        with pytest.raises(ValueError):
            lifecycle.JupyterTarget(url, "generated-jupyter-token", KERNEL, bridge_host)


def fake_boundaries(monkeypatch, lifecycle, *, failure=None, bridge_port=0):
    """Keep bridge/Jupyter execution real; replace only HTTP, WebSocket and sockets."""
    from craft import client as sdk

    actual_http = httpx.AsyncClient
    recorded = SimpleNamespace(
        sockets=[], servers=[], messages=[], http=[], order=[], namespace={}, failure=failure
    )

    class Socket:
        closed = False

        def __init__(self, *_):
            recorded.sockets.append(self)

        def bind(self, address):
            assert address == ("127.0.0.1", bridge_port)
            self.address = address
            if recorded.failure == "bind":
                raise OSError("generated occupied bridge port")

        def listen(self, _):
            pass

        def setblocking(self, value):
            assert value is False

        def setsockopt(self, *_):
            pass

        def getsockname(self):
            return ("127.0.0.1", bridge_port or 42424)

        def close(self):
            self.closed = True
            recorded.order.append("socket_closed")

    class Server:
        def __init__(self, config):
            self.config, self.ready = config, asyncio.Event()
            self.started = self.should_exit = False
            recorded.servers.append(self)

        async def serve(self, *, sockets):
            assert sockets == [recorded.sockets[-1]]
            assert not self.config.access_log and self.config.lifespan == "off"
            self.started = True
            self.ready.set()
            try:
                while not self.should_exit:
                    await asyncio.sleep(0.001)
            finally:
                recorded.order.append("server_closed")

    class Channel:
        def __init__(self):
            self.replies = asyncio.Queue()

        async def send(self, payload):
            message = json.loads(payload)
            recorded.messages.append(message)
            content = message["content"]
            assert content["silent"] is True and content["store_history"] is False
            assert content["allow_stdin"] is False
            if recorded.failure == "cancel":
                raise asyncio.CancelledError()
            if len(recorded.messages) == 1:
                await asyncio.sleep(0.02)
            exec(content["code"], recorded.namespace)
            if recorded.failure == "binding" and len(recorded.messages) == 1:
                recorded.namespace["_craft_binding_ok"] = False
            expressions = {
                name: {
                    "status": "ok",
                    "data": {"text/plain": repr(eval(expression, recorded.namespace))},
                }
                for name, expression in content["user_expressions"].items()
            }
            await self.replies.put(
                json.dumps(
                    {
                        "channel": "iopub",
                        "header": {"msg_type": "status"},
                        "parent_header": {"msg_id": "unrelated"},
                        "content": {"execution_state": "idle"},
                    }
                )
            )
            await self.replies.put(
                json.dumps(
                    {
                        "channel": "shell",
                        "header": {"msg_type": "execute_reply"},
                        "parent_header": {"msg_id": message["header"]["msg_id"]},
                        "content": {"status": "ok", "user_expressions": expressions},
                    }
                )
            )

        async def recv(self):
            return await self.replies.get()

    @asynccontextmanager
    async def open_channel(url, **kwargs):
        assert url == f"ws://127.0.0.1:9998/api/kernels/{KERNEL}/channels"
        assert kwargs["additional_headers"] == {"Authorization": "token generated-jupyter-token"}
        assert kwargs["proxy"] is None
        assert not kwargs["logger"].isEnabledFor(50)
        try:
            yield Channel()
        finally:
            recorded.order.append("kernel_channel_closed")

    def reply(request):
        recorded.http.append(request)
        assert not request.url.query and "cookie" not in request.headers
        assert request.headers["authorization"] == "token generated-jupyter-token"
        if request.url.path == f"/api/kernels/{KERNEL}":
            value = {"id": KERNEL, "name": "python3", "execution_state": "idle"}
        elif request.url.path == "/api/kernelspecs":
            value = {"kernelspecs": {"python3": {"spec": {"language": "python"}}}}
        elif request.url.path == "/api/sessions":
            value = [
                {"id": str(uuid4()), "path": NOTEBOOK, "type": "notebook", "kernel": {"id": KERNEL}}
            ]
            if recorded.failure == "session":
                value.append(dict(value[0], path="other.ipynb"))
        else:
            raise AssertionError(request.url.path)
        return httpx.Response(200, json=value)

    def jupyter_http(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return actual_http(transport=httpx.MockTransport(reply), **kwargs)

    def native_http(**kwargs):
        return actual_http(
            transport=httpx.ASGITransport(app=recorded.servers[0].config.app), **kwargs
        )

    monkeypatch.setattr(lifecycle, "httpx", SimpleNamespace(AsyncClient=jupyter_http))
    monkeypatch.setattr(lifecycle, "open_channel", open_channel)
    monkeypatch.setattr(lifecycle, "_BridgeServer", Server)
    monkeypatch.setattr(
        lifecycle, "socket", SimpleNamespace(socket=Socket, AF_INET=2, SOCK_STREAM=1)
    )
    monkeypatch.setattr(sdk, "httpx", SimpleNamespace(AsyncClient=native_http))
    return recorded


def owned_controller(lifecycle, run, *, bridge_port=0):
    from app.agent.workspace_session import RunBoundSession

    target = lifecycle.JupyterTarget(
        "http://127.0.0.1:9998/", "generated-jupyter-token", KERNEL, "host.docker.internal"
    )
    if bridge_port:
        target = replace(target, bridge_port=bridge_port)
    middleware = lifecycle.WorkspaceToolCallMiddleware(
        run.trace,
        run.budget,
        20,
        run.skills.completed,
        external_tools={"workspace_execute_code", "workspace_use_notebook"},
        kernel_id=KERNEL,
    )
    session = RunBoundSession(
        run.transport, middleware, context=run.parent, deadline=run.session._deadline
    )
    return lifecycle.WorkspaceRunController(session, target), session, middleware


@pytest.mark.parametrize("bridge_port", [0, 54301])
def test_controller_provisions_silently_and_native_sdk_uses_the_borrowed_budget(
    monkeypatch, bridge_port
):
    from craft import client as sdk
    from craft import native

    lifecycle = lifecycle_module()
    recorded = fake_boundaries(monkeypatch, lifecycle, bridge_port=bridge_port)

    async def execute():
        async with bound() as run:
            controller, session, middleware = owned_controller(
                lifecycle, run, bridge_port=bridge_port
            )
            try:
                instructions = await controller.start()
                assert NOTEBOOK in instructions and KERNEL in instructions
                assert "host.docker.internal" not in instructions
                assert recorded.sockets[0].address == ("127.0.0.1", bridge_port)
                assert sdk._binding.url == f"http://host.docker.internal:{bridge_port or 42424}/mcp"
                assert sdk._binding.run_id == str(run.parent.caller.run_id)
                assert sdk._binding.deadline <= session._deadline + 0.005
                assert session._middleware is middleware and middleware.budget is run.budget
                async with native() as client:
                    result = await client.search("owned generated source")
                assert result.structuredContent == {"hits": []}
                assert run.budget["calls"] == 1 and run.trace[0]["operation"] == "library.search"
                assert run.remote.calls[0][3].caller.run_id == run.parent.caller.run_id
            finally:
                await controller.close()
            assert sdk._binding is None and controller._bridge.bearer is None
            assert session._closed and run.transport.is_connected
            assert recorded.sockets[0].closed
            assert recorded.order.index("kernel_channel_closed") < recorded.order.index(
                "server_closed"
            )
            assert len(recorded.messages) == 2

    asyncio.run(execute())


def test_per_run_server_preserves_native_process_signals_and_logging(monkeypatch):
    lifecycle = lifecycle_module()
    server_type = lifecycle._BridgeServer
    access = logging.getLogger("uvicorn.access")
    sentinel = logging.NullHandler()
    monkeypatch.setattr(access, "handlers", [sentinel])
    monkeypatch.setattr(access, "propagate", True)
    recorded = fake_boundaries(monkeypatch, lifecycle)

    async def execute():
        async with bound() as run:
            controller, _, _ = owned_controller(lifecycle, run)
            try:
                await controller.start()
                assert access.handlers == [sentinel] and access.propagate is True
                server = server_type(recorded.servers[0].config)
                server.config.load()
                protocol = server.config.http_protocol_class(server.config, server.server_state, {})
                assert protocol.access_log is False
                previous = [signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)]
                with server.capture_signals():
                    assert [
                        signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)
                    ] == previous
            finally:
                await controller.close()

    asyncio.run(execute())


@pytest.mark.parametrize("failure", ["session", "binding", "cancel"])
def test_partial_provisioning_releases_bridge_socket_and_owner_lease(monkeypatch, failure):
    from craft import client as sdk

    lifecycle = lifecycle_module()
    recorded = fake_boundaries(monkeypatch, lifecycle, failure=failure)

    async def execute():
        async with bound() as run:
            controller, session, _ = owned_controller(lifecycle, run)
            try:
                error = asyncio.CancelledError if failure == "cancel" else RuntimeError
                with pytest.raises(error):
                    await controller.start()
            finally:
                recorded.failure = None
                await controller.close()
            assert session._closed and controller._bridge.bearer is None
            assert sdk._binding is None
            assert all(sock.closed for sock in recorded.sockets)
            retry, _, _ = owned_controller(lifecycle, run)
            try:
                await retry.start()
            finally:
                await retry.close()

    asyncio.run(execute())


@pytest.mark.parametrize("bridge_port", [0, 54301])
def test_overlapping_owner_run_cannot_overwrite_the_kernel_bearer(monkeypatch, bridge_port):
    from craft import client as sdk

    lifecycle = lifecycle_module()
    recorded = fake_boundaries(monkeypatch, lifecycle, bridge_port=bridge_port)

    async def execute():
        async with bound() as first, bound() as second:
            second.parent.caller = replace(
                second.parent.caller, user_id=first.parent.caller.user_id
            )
            active, _, _ = owned_controller(lifecycle, first, bridge_port=bridge_port)
            overlap, _, _ = owned_controller(lifecycle, second, bridge_port=bridge_port)
            try:
                await active.start()
                bearer = sdk._binding.run_token
                with pytest.raises(RuntimeError, match="already.*workspace"):
                    await overlap.start()
                assert sdk._binding.run_id == str(first.parent.caller.run_id)
                assert sdk._binding.run_token == bearer
                assert len(recorded.sockets) == 1
            finally:
                await overlap.close()
                assert sdk._binding.run_id == str(first.parent.caller.run_id)
                await active.close()

    asyncio.run(execute())


def test_kernel_memory_binding_expires_without_controller_cleanup():
    from craft import client as sdk

    assert hasattr(sdk, "native"), "The run-bound SDK accessor is missing"

    async def execute():
        run_id = str(uuid4())
        try:
            sdk._bind_run("http://127.0.0.1:12345/mcp", "generated-ephemeral", run_id, 0.01)
            old = sdk._binding
            await asyncio.sleep(0.03)
            assert sdk._binding is None
            assert old.run_token == ""
            with pytest.raises(RuntimeError, match="active.*binding"):
                async with sdk.native():
                    pass
        finally:
            sdk._unbind_run(run_id)

    asyncio.run(execute())


def test_delayed_pause_cleanup_cannot_erase_the_resumed_same_run_binding():
    from craft import client as sdk

    assert "binding_id" in inspect.signature(sdk._bind_run).parameters, (
        "Each provisioning attempt needs its own identity across pause/resume"
    )

    async def execute():
        run_id, paused, resumed = (str(uuid4()) for _ in range(3))
        try:
            sdk._bind_run(
                "http://127.0.0.1:12345/mcp", "paused-ephemeral", run_id, 30, binding_id=paused
            )
            assert sdk._unbind_run(paused)
            sdk._bind_run(
                "http://127.0.0.1:12346/mcp", "resumed-ephemeral", run_id, 30, binding_id=resumed
            )
            assert not sdk._unbind_run(paused)
            assert sdk._binding.run_id == run_id and sdk._binding.run_token == "resumed-ephemeral"
        finally:
            sdk._unbind_run(resumed)

    asyncio.run(execute())


def configure_jupyter(tmp_path, *, bridge_port=None):
    path = tmp_path / "private-workspaces.json"
    entries = json.loads(path.read_text())
    next(iter(entries.values()))["jupyter"] = {
        "url": "http://127.0.0.1:9998/",
        "token": "generated-jupyter-token",
        "kernel_id": KERNEL,
        "bridge_host": "host.docker.internal",
    }
    if bridge_port is not None:
        next(iter(entries.values()))["jupyter"]["bridge_port"] = bridge_port
    path.write_text(json.dumps(entries))


@pytest.mark.parametrize("bridge_port", [None, 54301])
def test_private_owner_configuration_selects_jupyter_without_exposing_credentials(
    monkeypatch, tmp_path, bridge_port
):
    from app.agent import runtime

    caller, _, _ = prepared_run(monkeypatch, tmp_path)
    configure_jupyter(tmp_path, bridge_port=bridge_port)
    selected = runtime._workspace_for_caller(caller)
    assert len(selected) == 3, "The root-selected Jupyter target is not wired into the run"
    transport, instructions, target = selected
    try:
        assert target.kernel_id == KERNEL and target.bridge_host == "host.docker.internal"
        assert target.bridge_port == (0 if bridge_port is None else bridge_port)
        assert "generated-jupyter-token" not in instructions + repr(target)
        assert "host.docker.internal" not in instructions
    finally:
        asyncio.run(transport.close())


@pytest.mark.parametrize("failure", [None, "execute", "cancel", "pause"])
def test_native_run_owns_bridge_cleanup_before_finish_and_transport_close(
    monkeypatch, tmp_path, failure
):
    from app.agent import runtime
    from app.infrastructure.mcp.client import LocalHTTPTool, ToolCallMiddleware

    assert hasattr(runtime, "WorkspaceRunController"), "The native run has no bridge lifecycle"
    caller, request, recorded = prepared_run(
        monkeypatch, tmp_path, failure=failure if failure != "pause" else None
    )
    configure_jupyter(tmp_path)
    phases, controllers = [], []

    class Controller:
        def __init__(self, session, target):
            self.session, self.target = session, target
            controllers.append(self)

        async def start(self):
            assert self.session._context.caller.user_id == caller.user_id
            assert self.session._context.caller.run_id == request.run_id
            assert self.session._context.caller.conversation_id == request.thread_id
            assert isinstance(self.session._middleware, ToolCallMiddleware)
            phases.append("bridge_started")
            return "\nNative SDK access is active in the selected notebook."

        async def close(self):
            phases.append("bridge_closed")
            await self.session.close()

    monkeypatch.setattr(runtime, "WorkspaceRunController", Controller)
    original_finish, original_close = runtime.service.finish, LocalHTTPTool.close

    def finish(*args, **kwargs):
        phases.append("run_finished")
        original_finish(*args, **kwargs)

    async def close(transport):
        phases.append(transport.name + "_closed")
        await original_close(transport)

    monkeypatch.setattr(runtime.service, "finish", finish)
    monkeypatch.setattr(LocalHTTPTool, "close", close)
    if failure == "pause":

        class PausingAdapter:
            def __init__(self, *_args, **_kwargs):
                pass

            async def run(self, _data):
                yield SimpleNamespace(
                    model_dump=lambda **_: {
                        "type": "RUN_FINISHED",
                        "outcome": {
                            "type": "interrupt",
                            "interrupts": [
                                {
                                    "id": "generated-interrupt",
                                    "metadata": {
                                        "agent_framework": {
                                            "function_call": {
                                                "name": "write_note",
                                                "call_id": "generated-gated-call",
                                                "arguments": {
                                                    "title": "Generated",
                                                    "body": "Owned",
                                                },
                                            }
                                        }
                                    },
                                }
                            ],
                        },
                    }
                )

        monkeypatch.setattr(runtime, "DurableApprovalAgent", PausingAdapter)
        monkeypatch.setattr(runtime, "request_approval", lambda *_: SimpleNamespace(id=uuid4()))

    async def execute():
        async with bound() as context:
            context.parent.caller = caller
            token = current_call.set(context.parent)
            try:
                if failure == "cancel":
                    with pytest.raises(asyncio.CancelledError):
                        return [event async for event in runtime._run(caller, request)]
                    return []
                return [event async for event in runtime._run(caller, request)]
            finally:
                current_call.reset(token)

    events = asyncio.run(execute())
    assert len(controllers) == 1 and controllers[0].session._closed
    assert (
        phases.index("bridge_closed") < phases.index("run_finished") < phases.index("craft_closed")
    )
    middleware = recorded["agents"][0].options["middleware"][0]
    assert middleware is controllers[0].session._middleware
    finished_args = recorded["finished"][0][0]
    assert middleware.budget["calls"] == finished_args[3]["state"]["craft"]["toolCalls"]
    assert "generated-jupyter-token" not in repr(recorded["agents"][0].options)
    assert "generated-jupyter-token" not in repr(finished_args[3])
    assert (
        finished_args[2]
        == {None: "finished", "execute": "failed", "cancel": "cancelled", "pause": "paused"}[
            failure
        ]
    )
    if failure in {None, "pause"}:
        assert events[-1].type == "RUN_FINISHED"


@pytest.mark.parametrize("bridge_port", [0, 54301, 65535])
def test_jupyter_target_bridge_port_is_optional_and_validated(bridge_port):
    lifecycle = lifecycle_module()
    default = lifecycle.JupyterTarget("http://127.0.0.1:9998/", "generated-jupyter-token", KERNEL)
    target = replace(default, bridge_port=bridge_port)
    assert default.bridge_port == 0
    assert target.bridge_port == bridge_port
    assert target.bridge_host == "127.0.0.1"


@pytest.mark.parametrize("bridge_port", [-1, 65536, True, False, "54301", 54301.0, None])
def test_jupyter_target_rejects_invalid_bridge_port(bridge_port):
    lifecycle = lifecycle_module()
    with pytest.raises(ValueError):
        lifecycle.JupyterTarget(
            "http://127.0.0.1:9998/",
            "generated-jupyter-token",
            KERNEL,
            bridge_port=bridge_port,
        )


def test_fixed_bridge_port_collision_never_falls_back_and_releases_owner(monkeypatch):
    from craft import client as sdk

    lifecycle = lifecycle_module()
    recorded = fake_boundaries(monkeypatch, lifecycle, failure="bind", bridge_port=54301)

    async def execute():
        async with bound() as run:
            controller, session, _ = owned_controller(lifecycle, run, bridge_port=54301)
            try:
                with pytest.raises(OSError, match="occupied bridge port"):
                    await controller.start()
            finally:
                await controller.close()
            assert session._closed and controller._bridge.bearer is None
            assert sdk._binding is None and recorded.sockets[0].closed
            assert len(recorded.sockets) == 1
            assert not recorded.servers and not recorded.messages
            recorded.failure = None
            retry, _, _ = owned_controller(lifecycle, run, bridge_port=54301)
            try:
                await retry.start()
                assert sdk._binding.url == "http://host.docker.internal:54301/mcp"
            finally:
                await retry.close()
            assert sdk._binding is None and all(sock.closed for sock in recorded.sockets)

    asyncio.run(execute())
