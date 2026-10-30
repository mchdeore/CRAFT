"""Root-owned serving and memory-only provisioning for one existing native run."""

import asyncio
import json
import logging
import socket
import time
from contextlib import AsyncExitStack, contextmanager
from threading import Lock
from uuid import uuid4

import uvicorn
from agent_framework.exceptions import ToolExecutionException
from uvicorn.protocols.http.h11_impl import H11Protocol
from websockets.asyncio.client import connect as open_channel

from app.agent.workspace_bridge import WorkspaceRunBridge
from app.agent.workspace_session import RunBoundSession
from app.infrastructure.mcp.client import ToolCallMiddleware
from app.infrastructure.workspaces import JupyterTarget, channels_url, jupyter_http

_owners: set[str] = set()
_owners_lock = Lock()
_channel_logger = logging.Logger("craft.kernel-provision", level=logging.CRITICAL + 1)
logger = logging.getLogger("craft.workspace")


class WorkspaceToolCallMiddleware(ToolCallMiddleware):
    """Pin the already-declared Jupyter tool arguments inside the shared middleware."""

    def __init__(self, *args, kernel_id, **kwargs):
        super().__init__(*args, **kwargs)
        self._kernel_id, self._notebook = kernel_id, None

    def pin_notebook(self, path):
        self._notebook = path

    async def process(self, context, call_next):
        async def dispatch():
            remote = (context.function.additional_properties or {}).get("_mcp_remote_name")
            if context.function.name in self.external_tools and remote in {
                "execute_code",
                "use_notebook",
            }:
                if context.arguments.get("kernel_id") not in {None, self._kernel_id}:
                    raise ToolExecutionException("The workspace kernel is pinned for this run.")
                if remote == "use_notebook" and (
                    self._notebook is None
                    or context.arguments.get("notebook_path") != self._notebook
                ):
                    raise ToolExecutionException("The workspace notebook is pinned for this run.")
                context.arguments["kernel_id"] = self._kernel_id
            await call_next()

        await super().process(context, dispatch)


class _BridgeConfig(uvicorn.Config):
    def configure_logging(self):
        # Config(access_log=False) otherwise clears the main server's handlers.
        pass


class _BridgeHTTPProtocol(H11Protocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Uvicorn otherwise derives access logging from the shared logger's handlers.
        self.access_log = False
        # The HTTP-only bridge is already ASGI3; omit optional trace/proxy wrappers.
        self.app = self.config.app


class _BridgeServer(uvicorn.Server):
    def __init__(self, config):
        super().__init__(config)
        self.ready = asyncio.Event()

    @contextmanager
    def capture_signals(self):
        # A per-run server must not replace the native application's signal handlers.
        yield

    async def startup(self, sockets=None):
        try:
            await super().startup(sockets=sockets)
        finally:
            self.ready.set()


class WorkspaceRunController:
    """Borrow one transport/context/deadline; issue no native login or persistent run."""

    def __init__(self, session: RunBoundSession, target: JupyterTarget):
        if not isinstance(session._middleware, WorkspaceToolCallMiddleware):
            raise ValueError("Expected the native run's pinned workspace middleware")
        self._session, self._target = session, target
        self._middleware = session._middleware
        self._bridge = WorkspaceRunBridge(session, target.bridge_host)
        self._owner = str(session._context.caller.user_id)
        self._run_id = str(session._context.caller.run_id)
        self._binding_id = str(uuid4())
        self._resources = AsyncExitStack()
        self._server: _BridgeServer | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._started = self._closed = self._provision_attempted = False

    def _release_owner(self):
        with _owners_lock:
            _owners.discard(self._owner)

    async def _select_notebook(self, http):
        responses = await asyncio.gather(
            http.get(f"api/kernels/{self._target.kernel_id}"),
            http.get("api/kernelspecs"),
            http.get("api/sessions"),
        )
        for response in responses:
            response.raise_for_status()
        kernel, specs, sessions = [response.json() for response in responses]
        language = (
            specs.get("kernelspecs", {}).get(kernel.get("name"), {}).get("spec", {}).get("language")
        )
        if kernel.get("id") != self._target.kernel_id or language != "python":
            raise RuntimeError("The selected owner kernel must be an active Python kernel.")
        matching = [
            row
            for row in sessions
            if row.get("type") == "notebook"
            and row.get("kernel", {}).get("id") == self._target.kernel_id
        ]
        if len(matching) != 1:
            raise RuntimeError("The selected owner kernel needs exactly one notebook session.")
        path = matching[0].get("path")
        if (
            not isinstance(path, str)
            or not path.endswith(".ipynb")
            or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or any(ord(value) < 32 for value in path)
        ):
            raise RuntimeError("The selected notebook must have an owned relative path.")
        return path

    def _kernel_channel(self):
        """A channel to the selected kernel for one bind or unbind, closed right after it.

        Kept open, it would take every broadcast of the kernel, and one large frame could close
        it before the unbind.
        """
        return open_channel(
            channels_url(self._target),
            additional_headers={"Authorization": "token " + self._target.token},
            proxy=None,
            logger=_channel_logger,
            open_timeout=10,
            close_timeout=2,
            max_size=8 * 1024 * 1024,
        )

    async def _execute(self, channel, code, deadline):
        from jupyter_client.session import Session

        session = Session(username="craft-controller")
        message = session.msg(
            "execute_request",
            content={
                "code": code,
                "silent": True,
                "store_history": False,
                "user_expressions": {"craft_binding_ok": "_craft_binding_ok"},
                "allow_stdin": False,
                "stop_on_error": True,
            },
        )
        message["channel"] = "shell"
        async with asyncio.timeout_at(deadline):
            await channel.send(session.pack(message).decode())
            while True:
                reply = json.loads(await channel.recv())
                if (
                    reply.get("channel") != "shell"
                    or reply.get("parent_header", {}).get("msg_id") != message["header"]["msg_id"]
                    or reply.get("header", {}).get("msg_type") != "execute_reply"
                ):
                    continue
                content = reply.get("content", {})
                expression = content.get("user_expressions", {}).get("craft_binding_ok", {})
                if (
                    content.get("status") != "ok"
                    or expression.get("status") != "ok"
                    or expression.get("data", {}).get("text/plain") != "True"
                ):
                    raise RuntimeError("The selected kernel refused its active CRAFT run binding.")
                return

    async def _stop_server(self):
        server, task = self._server, self._server_task
        if server is None or task is None:
            return
        server.should_exit = True
        try:
            async with asyncio.timeout(5):
                await asyncio.gather(task, return_exceptions=True)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def start(self) -> str:
        if self._started or self._closed:
            raise RuntimeError("The workspace run lifecycle is single-use.")
        self._started = True
        with _owners_lock:
            busy = self._owner in _owners
            if not busy:
                _owners.add(self._owner)
        if busy:
            # The kernel takes one binding; run_code reaches it over its own channel.
            return (
                "\nNative SDK access (`craft.native()`) is busy in another of the owner's runs, "
                "so it is unavailable in this one. run_code still executes in the kernel after "
                "approval."
            )
        self._resources.callback(self._release_owner)
        async with asyncio.timeout_at(self._session._deadline):
            self._session._check_open()
            http = await self._resources.enter_async_context(jupyter_http(self._target))
            notebook = await self._select_notebook(http)
            self._middleware.pin_notebook(notebook)
            await self._resources.enter_async_context(self._bridge.run())
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._resources.callback(listener.close)
            # On the cluster the person's pod connects to this pod's own address (bridge_host);
            # the network policy lets only workspace pods reach it, with the run's bearer.
            loopback = {"127.0.0.1", "localhost", "host.docker.internal"}
            listen = self._target.bridge_host
            if listen in loopback:
                listen = "127.0.0.1"
            listener.bind((listen, self._target.bridge_port))
            listener.listen(128)
            listener.setblocking(False)
            port = listener.getsockname()[1]
            server = self._server = _BridgeServer(
                _BridgeConfig(
                    self._bridge,
                    http=_BridgeHTTPProtocol,
                    interface="asgi3",
                    lifespan="off",
                    ws="none",
                    log_config=None,
                    access_log=False,
                    timeout_graceful_shutdown=0,
                )
            )
            self._server_task = asyncio.create_task(
                self._server.serve(sockets=[listener]), name="craft.workspace-http"
            )
            self._server_task.add_done_callback(lambda _: server.ready.set())
            self._resources.push_async_callback(self._stop_server)
            await self._server.ready.wait()
            if not self._server.started or self._server_task.done():
                raise RuntimeError("The workspace run bridge could not start.")
            bearer = self._bridge.bearer
            if bearer is None:
                raise RuntimeError("The workspace run bridge is unavailable.")
            expires = time.time() + self._session._deadline - asyncio.get_running_loop().time()
            async with self._kernel_channel() as channel:
                self._provision_attempted = True
                await self._execute(
                    channel,
                    "_craft_binding_ok = False\ntry:\n"
                    "    __import__('craft.client', fromlist=['_bind_run'])._bind_run("
                    f"{f'http://{self._target.bridge_host}:{port}/mcp'!r}, "
                    f"{bearer.get_secret_value()!r}, {self._run_id!r}, "
                    f"{expires!r} - __import__('time').time(), binding_id={self._binding_id!r})\n"
                    "    _craft_binding_ok = True\nexcept BaseException:\n    pass\n",
                    self._session._deadline,
                )
            return (
                f"\nNative Python SDK access is active only in notebook {json.dumps(notebook)} "
                f"and kernel {self._target.kernel_id} during this run. "
                "run_code executes in this kernel after approval. "
                "Use `import craft` for files, documents and read access points such as "
                "`craft.search` and `craft.cube.find`; `async with craft.native() as sdk:` "
                "remains available for run-bound reads. "
                "Other kernels and standalone processes have no active binding. "
                "Native mutations still require the CRAFT approval/resume path."
            )

    async def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            # Withdraw before kernel cleanup, persistent run finish or native transport close.
            await self._bridge.close()
        finally:
            try:
                if self._provision_attempted:
                    deadline = asyncio.get_running_loop().time() + 5
                    async with asyncio.timeout_at(deadline), self._kernel_channel() as channel:
                        await self._execute(
                            channel,
                            "_craft_binding_ok = False\ntry:\n"
                            "    _craft_binding_ok = __import__('craft.client', "
                            "fromlist=['_unbind_run'])._unbind_run("
                            f"{self._binding_id!r})\nexcept BaseException:\n    pass\n",
                            deadline,
                        )
            except Exception as error:
                # The bridge is already withdrawn, so a binding left behind can't reach it, and
                # it clears itself at the original run deadline.
                logger.warning(
                    "workspace unbind failed",
                    extra={"fields": {"run_id": self._run_id, "error_type": type(error).__name__}},
                )
            finally:
                await self._resources.aclose()
