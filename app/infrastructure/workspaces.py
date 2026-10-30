"""Each owner's private Jupyter server, reached through the Hub (app/infrastructure/hub.py).

The server address comes from the caller's own user ID. Nothing here is chosen by request or
model arguments.
"""

import asyncio
import base64
import binascii
import ipaddress
import json
import logging
import re
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx

from app.infrastructure import hub
from app.infrastructure.errors import ApiError, RunTimeout, WorkspaceUnavailable

RUN_SECONDS = 100  # below the agent's 120 s MCP request timeout, so it hears run_timeout
OUTPUT_LIMIT = 64 * 1024
IMAGE_LIMIT = 1024 * 1024
IMAGE_COUNT = 2
SVG_LIMIT = 2 * 1024 * 1024  # a larger SVG isn't rendered
TRACEBACK_LINES = 20
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_channel_logger = logging.Logger("craft.kernel-actions", level=logging.CRITICAL + 1)


def bridge_host_allowed(host: str) -> bool:
    """Loopback names, or a private address: the API pod's own on the cluster."""
    if host in {"127.0.0.1", "localhost", "host.docker.internal"}:
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


@dataclass(frozen=True)
class JupyterTarget:
    url: str = field(repr=False)
    token: str = field(repr=False)
    kernel_id: str = ""  # only code runs need one
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
            or (self.kernel_id and str(UUID(self.kernel_id)) != self.kernel_id)
            or not bridge_host_allowed(self.bridge_host)
            or not isinstance(self.bridge_port, int)
            or isinstance(self.bridge_port, bool)
            or not 0 <= self.bridge_port <= 65535
        ):
            raise ValueError(
                "Expected a private Jupyter root, token, selected kernel and bridge port"
            )
        object.__setattr__(self, "url", self.url.rstrip("/") + "/")


def owned_name(path: str) -> str:
    """A relative workspace path without traversal, hidden parts or control characters."""
    parts = path.split("/")
    if (
        not path
        or len(path) > 255
        or "\\" in path
        or path.startswith("/")
        or any(ord(value) < 32 for value in path)
        or any(part in {"", ".", ".."} or part.startswith(".") for part in parts)
    ):
        raise ValueError("Expected a relative owned workspace path")
    return path


def jupyter_http(
    target: JupyterTarget, timeout: float = 120
) -> AbstractAsyncContextManager[httpx.AsyncClient]:
    return hub.client(target.url, target.token, timeout)


def channels_url(target: JupyterTarget) -> str:
    """The websocket of the selected kernel, on the target's own server."""
    scheme = urlsplit(target.url).scheme
    channels = target.url + f"api/kernels/{target.kernel_id}/channels"
    return ("wss" if scheme == "https" else "ws") + channels[len(scheme) :]


async def run_in_kernel(
    target: JupyterTarget, code: str, seconds: float = RUN_SECONDS
) -> dict[str, Any]:
    """Execute code once in the owner's selected kernel and collect its visible outputs.

    After ``seconds`` the call answers ``RunTimeout``. Code the kernel has started is
    interrupted, as JupyterLab's stop button does; a request still queued behind the owner's own
    cell leaves that cell running.
    """
    if not target.kernel_id:
        raise ValueError("No kernel is selected")
    from jupyter_client.session import Session
    from websockets.asyncio.client import connect as open_channel
    from websockets.exceptions import WebSocketException

    session = Session(username="craft-workspace")
    message = session.msg(
        "execute_request",
        content={
            "code": code,
            "silent": False,
            "store_history": True,
            "user_expressions": {},
            "allow_stdin": False,
            "stop_on_error": True,
        },
    )
    message["channel"] = "shell"
    identifier = message["header"]["msg_id"]
    collected = KernelOutputs()
    sent = started = False
    try:
        async with asyncio.timeout(seconds):
            async with open_channel(
                channels_url(target),
                additional_headers={"Authorization": "token " + target.token},
                proxy=None,
                logger=_channel_logger,
                open_timeout=10,
                close_timeout=2,
                max_size=8 * 1024 * 1024,
            ) as channel:
                await channel.send(session.pack(message).decode())
                sent = True
                replied = idle = False
                while not (replied and idle):
                    reply = json.loads(await channel.recv())
                    if reply.get("parent_header", {}).get("msg_id") != identifier:
                        continue
                    started = True  # the kernel answers this request only once it runs it
                    kind = reply.get("header", {}).get("msg_type")
                    content = reply.get("content", {})
                    if kind == "status" and content.get("execution_state") == "idle":
                        idle = True
                    elif kind == "execute_reply":
                        replied = True
                    collected.add(kind, content)
    except TimeoutError:
        if not sent:
            raise WorkspaceUnavailable() from None  # the kernel's channel didn't open in time
        if started:
            await _interrupt(target)
        raise RunTimeout() from None
    except (OSError, WebSocketException):
        raise WorkspaceUnavailable() from None
    return await collected.result()


async def _interrupt(target: JupyterTarget) -> None:
    """Best effort, bounded: a kernel that can't be reached is left to its own code."""
    with suppress(ApiError, httpx.HTTPError):
        async with jupyter_http(target, timeout=5) as http:
            await http.post(f"api/kernels/{target.kernel_id}/interrupt")


class KernelOutputs:
    """Visible text (64 KB), up to two images (1 MB each) and a bounded traceback.

    An SVG is kept as it came and becomes a PNG in ``result``, in a thread once the channel is
    closed. vl_convert holds the GIL while it renders, so the event loop still waits for it.
    """

    def __init__(self):
        self.outputs: list[str] = []
        self.images: list[dict[str, str]] = []
        self.traceback: list[str] = []
        self.size = 0
        self.truncated = False
        self.status = "ok"
        self.error: str | None = None

    def keep(self, text: str) -> None:
        if self.size >= OUTPUT_LIMIT:
            self.truncated = True
            return
        piece = text[: OUTPUT_LIMIT - self.size]
        self.truncated = self.truncated or len(piece) < len(text)
        self.size += len(piece)
        self.outputs.append(piece)

    def image(self, data: dict[str, Any]) -> bool:
        """Keep one PNG, JPEG or SVG and report whether it was an image."""
        found = None
        for media in ("image/png", "image/jpeg"):
            if isinstance(data.get(media), str):
                try:
                    found = (media, base64.b64decode(data[media], validate=False))
                except (binascii.Error, ValueError):
                    return True
                break
        if found is None and isinstance(data.get("image/svg+xml"), str | list):
            svg = data["image/svg+xml"]
            found = ("image/svg+xml", ("".join(svg) if isinstance(svg, list) else svg).encode())
        if found is None:
            return False
        media, raw = found
        limit = SVG_LIMIT if media == "image/svg+xml" else IMAGE_LIMIT
        if len(self.images) >= IMAGE_COUNT or len(raw) > limit:
            self.truncated = True
        elif media == "image/svg+xml":
            self.images.append({"media_type": media, "svg": raw.decode()})
        else:
            self.images.append({"media_type": media, "data_base64": base64.b64encode(raw).decode()})
        return True

    def add(self, kind: str | None, content: dict[str, Any]) -> None:
        if kind == "stream":
            self.keep(content.get("text", ""))
        elif kind in {"execute_result", "display_data"}:
            data = content.get("data", {})
            shown = self.image(data)
            if "text/plain" in data and not shown:
                self.keep(str(data["text/plain"]) + "\n")
            elif shown:
                self.keep(f"[image output {len(self.images)}]\n")
            elif data:
                self.keep(f"[{', '.join(sorted(data))} output]\n")
        elif kind == "error":
            self.status = "error"
            name = content.get("ename", "Error")
            self.error = f"{name}: {content.get('evalue', '')}"[:2000]
            lines = [
                _ANSI.sub("", line)[:500]
                for entry in content.get("traceback", [])
                if isinstance(entry, str)
                for line in entry.splitlines()
            ]
            self.traceback = lines[-TRACEBACK_LINES:]
        elif kind == "execute_reply" and content.get("status") != "ok":
            self.status = "error"
            self.error = self.error or content.get("ename", "Execution failed")

    async def result(self) -> dict[str, Any]:
        """Each kept SVG becomes a PNG in a thread; one that fails or comes out too large goes."""
        images = []
        for image in self.images:
            if "svg" in image:
                import vl_convert

                try:
                    raw = await asyncio.to_thread(vl_convert.svg_to_png, image["svg"])
                except (ValueError, RuntimeError):
                    continue
                if len(raw) > IMAGE_LIMIT:
                    self.truncated = True
                    continue
                image = {"media_type": "image/png", "data_base64": base64.b64encode(raw).decode()}
            images.append(image)
        return {
            "status": self.status,
            "outputs": self.outputs,
            "error": self.error,
            "truncated": self.truncated,
            "traceback": self.traceback,
            "images": images,
        }


def _contents(path: str) -> str:
    return "api/contents/" + quote(path, safe="/")


async def _ensure_folders(http: httpx.AsyncClient, path: str) -> None:
    parts = path.split("/")[:-1]
    for depth in range(1, len(parts) + 1):
        folder = "/".join(parts[:depth])
        found = await http.get(_contents(folder), params={"content": "0"})
        if found.status_code == 404:
            hub.checked(await http.put(_contents(folder), json={"type": "directory"}))
        else:
            if hub.checked(found).json().get("type") != "directory":
                raise ValueError("A parent of the workspace path is not a folder")


async def write_text(target: JupyterTarget, path: str, content: str) -> dict[str, Any]:
    """Create or replace one owned text file through the Jupyter contents service."""
    async with jupyter_http(target) as http:
        await _ensure_folders(http, path)
        saved = await http.put(
            _contents(path), json={"type": "file", "format": "text", "content": content}
        )
        return hub.checked(saved).json()


async def read_notebook(target: JupyterTarget, path: str) -> dict[str, Any] | None:
    """The notebook, or None when Jupyter says it is missing; any other failure raises."""
    async with jupyter_http(target) as http:
        found = await http.get(_contents(path), params={"type": "notebook", "content": "1"})
        if found.status_code == 404:
            return None
        return hub.checked(found).json()["content"]


async def write_notebook(target: JupyterTarget, path: str, notebook: dict[str, Any]) -> None:
    async with jupyter_http(target) as http:
        await _ensure_folders(http, path)
        hub.checked(await http.put(_contents(path), json={"type": "notebook", "content": notebook}))


async def read_folder(target: JupyterTarget, folder: str, limit: int) -> dict[str, bytes]:
    """Every visible file under one owned folder, relative to it; at most ``limit`` bytes."""
    found: dict[str, bytes] = {}
    size = 0
    async with jupyter_http(target) as http:
        pending = [folder]
        while pending:
            current = pending.pop()
            listing = await http.get(_contents(current) if current else "api/contents/")
            for entry in hub.checked(listing).json().get("content") or []:
                if entry["name"].startswith("."):
                    continue
                if entry["type"] == "directory":
                    pending.append(entry["path"])
                    continue
                size += entry.get("size") or 0
                if size > limit:
                    raise ValueError("The notebook folder is too large")
                item = await http.get(
                    _contents(entry["path"]), params={"format": "base64", "type": "file"}
                )
                content = base64.b64decode(hub.checked(item).json()["content"])
                size += len(content) - (entry.get("size") or 0)
                if size > limit:
                    raise ValueError("The notebook folder is too large")
                found[entry["path"][len(folder) + 1 :] if folder else entry["path"]] = content
    return found


async def exists(target: JupyterTarget, path: str) -> bool:
    async with jupyter_http(target) as http:
        found = await http.get(_contents(path), params={"content": "0"})
        if found.status_code == 404:
            return False
        hub.checked(found)
        return True


async def write_bytes(target: JupyterTarget, path: str, content: bytes) -> None:
    """Create one owned file through the Jupyter contents service."""
    async with jupyter_http(target) as http:
        await _ensure_folders(http, path)
        saved = await http.put(
            _contents(path),
            json={
                "type": "file",
                "format": "base64",
                "content": base64.b64encode(content).decode(),
            },
        )
        hub.checked(saved)
