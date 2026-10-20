"""Root-owned serving and memory-only provisioning for one existing native run."""

import asyncio
import json
import logging
import socket
import time
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass, field
from http.cookiejar import CookieJar, DefaultCookiePolicy
from threading import Lock
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import uvicorn
from agent_framework.exceptions import ToolExecutionException
from uvicorn.protocols.http.h11_impl import H11Protocol
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as open_channel

from app.agent.workspace_bridge import WorkspaceRunBridge
from app.agent.workspace_session import RunBoundSession
from app.infrastructure.mcp.client import ToolCallMiddleware

_owners: set[str] = set()
_owners_lock = Lock()
_channel_logger = logging.Logger("craft.kernel-provision", level=logging.CRITICAL + 1)


@dataclass(frozen=True)
class JupyterTarget:
    url: str = field(repr=False)
    token: str = field(repr=False)
    kernel_id: str
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 0

    def __post_init__(self):
        target = urlsplit(self.url)
        if (
            target.scheme not in {"http", "https"}
            or not target.hostname
            or target.username is not None
            or target.password is not None
            or target.query
            or target.fragment
            or not self.token
            or any(value.isspace() for value in self.token)
            or str(UUID(self.kernel_id)) != self.kernel_id
            or self.bridge_host not in {"127.0.0.1", "localhost", "host.docker.internal"}
            or not isinstance(self.bridge_port, int)
            or isinstance(self.bridge_port, bool)
            or not 0 <= self.bridge_port <= 65535
        ):
            raise ValueError(
                "Expected a private Jupyter root, token, selected kernel and bridge port"
            )
        object.__setattr__(self, "url", self.url.rstrip("/") + "/")


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
        self._bridge = WorkspaceRunBridge(session)
        self._owner = str(session._context.caller.user_id)
        self._run_id = str(session._context.caller.run_id)
        self._binding_id = str(uuid4())
        self._resources = AsyncExitStack()
        self._channel: ClientConnection | None = None
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

    async def _execute(self, code, deadline):
        from jupyter_client.session import Session

        channel = self._channel
        if channel is None:
            raise RuntimeError("The selected kernel channel is unavailable.")
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
            if self._owner in _owners:
                raise RuntimeError("This owner already has an active workspace run.")
            _owners.add(self._owner)
        self._resources.callback(self._release_owner)
        async with asyncio.timeout_at(self._session._deadline):
            self._session._check_open()
            http = await self._resources.enter_async_context(
                httpx.AsyncClient(
                    base_url=self._target.url,
                    headers={"Authorization": "token " + self._target.token},
                    cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
                    trust_env=False,
                    follow_redirects=False,
                    timeout=120,
                )
            )
            notebook = await self._select_notebook(http)
            self._middleware.pin_notebook(notebook)
            await self._resources.enter_async_context(self._bridge.run())
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._resources.callback(listener.close)
            listener.bind(("127.0.0.1", self._target.bridge_port))
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
            target = urlsplit(self._target.url)
            channels = self._target.url + f"api/kernels/{self._target.kernel_id}/channels"
            channels = ("wss" if target.scheme == "https" else "ws") + channels[
                len(target.scheme) :
            ]
            self._channel = await self._resources.enter_async_context(
                open_channel(
                    channels,
                    additional_headers={"Authorization": "token " + self._target.token},
                    proxy=None,
                    logger=_channel_logger,
                    open_timeout=10,
                    close_timeout=2,
                )
            )
            self._provision_attempted = True
            bearer = self._bridge.bearer
            if bearer is None:
                raise RuntimeError("The workspace run bridge is unavailable.")
            expires = time.time() + self._session._deadline - asyncio.get_running_loop().time()
            await self._execute(
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
                "Keep workspace_use_notebook on that path; execution is pinned to this kernel. "
                "Use `from craft import native` and `async with native() as sdk:` for reads. "
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
                if self._channel is not None and self._provision_attempted:
                    await self._execute(
                        "_craft_binding_ok = False\ntry:\n"
                        "    _craft_binding_ok = __import__('craft.client', "
                        "fromlist=['_unbind_run'])._unbind_run("
                        f"{self._binding_id!r})\nexcept BaseException:\n    pass\n",
                        asyncio.get_running_loop().time() + 5,
                    )
            except Exception:
                # A lost kernel connection cannot retain a usable bridge capability;
                # the memory binding also clears itself at the original run deadline.
                pass
            finally:
                await self._resources.aclose()
                self._channel = None
