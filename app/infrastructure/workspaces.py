"""Each owner's private Jupyter workspace, selected only from the trusted server configuration.

The private file named by ``USER_WORKSPACES_FILE`` maps owner identifiers to their
workspace endpoints. Nothing here is chosen by request or model arguments.
"""

import asyncio
import base64
import binascii
import json
import logging
import os
import re
from dataclasses import dataclass, field
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx

OUTPUT_LIMIT = 64 * 1024
IMAGE_LIMIT = 1024 * 1024
IMAGE_COUNT = 2
TRACEBACK_LINES = 20
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_channel_logger = logging.Logger("craft.kernel-actions", level=logging.CRITICAL + 1)


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


def owner_entry(user_id: UUID | None) -> dict | None:
    """The owner's private workspace entry, or None when none is configured."""
    path = os.environ.get("USER_WORKSPACES_FILE")
    if not path or user_id is None:
        return None
    endpoints = json.loads(Path(path).read_text())
    if not isinstance(endpoints, dict):
        raise ValueError("Invalid private workspace configuration")
    entry = endpoints.get(str(user_id))
    if entry is not None and not isinstance(entry, dict):
        raise ValueError("Invalid private workspace configuration")
    return entry


def jupyter_target(entry: dict) -> JupyterTarget:
    config = entry.get("jupyter")
    if (
        not isinstance(config, dict)
        or any(not isinstance(config.get(key), str) for key in ("url", "token", "kernel_id"))
        or not isinstance(config.get("bridge_host", "127.0.0.1"), str)
    ):
        raise ValueError("Invalid private Jupyter configuration")
    return JupyterTarget(
        config["url"],
        config["token"],
        config["kernel_id"],
        config.get("bridge_host", "127.0.0.1"),
        bridge_port=config.get("bridge_port", 0),
    )


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


def jupyter_http(target: JupyterTarget) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=target.url,
        headers={"Authorization": "token " + target.token},
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        trust_env=False,
        follow_redirects=False,
        timeout=120,
    )


async def run_in_kernel(target: JupyterTarget, code: str, seconds: float = 120) -> dict[str, Any]:
    """Execute code once in the owner's selected kernel and collect its visible outputs."""
    from jupyter_client.session import Session
    from websockets.asyncio.client import connect as open_channel

    base = urlsplit(target.url)
    channels = target.url + f"api/kernels/{target.kernel_id}/channels"
    channels = ("wss" if base.scheme == "https" else "ws") + channels[len(base.scheme) :]
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
    async with asyncio.timeout(seconds):
        async with open_channel(
            channels,
            additional_headers={"Authorization": "token " + target.token},
            proxy=None,
            logger=_channel_logger,
            open_timeout=10,
            close_timeout=2,
            max_size=8 * 1024 * 1024,
        ) as channel:
            await channel.send(session.pack(message).decode())
            replied = idle = False
            while not (replied and idle):
                reply = json.loads(await channel.recv())
                if reply.get("parent_header", {}).get("msg_id") != identifier:
                    continue
                kind = reply.get("header", {}).get("msg_type")
                content = reply.get("content", {})
                if kind == "status" and content.get("execution_state") == "idle":
                    idle = True
                elif kind == "execute_reply":
                    replied = True
                collected.add(kind, content)
    return collected.result()


class KernelOutputs:
    """Visible text (64 KB), up to two images (1 MB each) and a bounded traceback."""

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
        """Keep one PNG or JPEG (an SVG becomes a PNG) and report whether it was an image."""
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
            svg = "".join(svg) if isinstance(svg, list) else svg
            try:
                import vl_convert

                found = ("image/png", vl_convert.svg_to_png(svg))
            except (ValueError, RuntimeError):
                return True
        if found is None:
            return False
        media, raw = found
        if len(self.images) >= IMAGE_COUNT or len(raw) > IMAGE_LIMIT:
            self.truncated = True
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

    def result(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "outputs": self.outputs,
            "error": self.error,
            "truncated": self.truncated,
            "traceback": self.traceback,
            "images": self.images,
        }


def _contents(path: str) -> str:
    return "api/contents/" + quote(path, safe="/")


async def _ensure_folders(http: httpx.AsyncClient, path: str) -> None:
    parts = path.split("/")[:-1]
    for depth in range(1, len(parts) + 1):
        folder = "/".join(parts[:depth])
        found = await http.get(_contents(folder), params={"content": "0"})
        if found.status_code == 404:
            (await http.put(_contents(folder), json={"type": "directory"})).raise_for_status()
        else:
            found.raise_for_status()
            if found.json().get("type") != "directory":
                raise ValueError("A parent of the workspace path is not a folder")


async def write_text(target: JupyterTarget, path: str, content: str) -> dict[str, Any]:
    """Create or replace one owned text file through the Jupyter contents service."""
    async with jupyter_http(target) as http:
        await _ensure_folders(http, path)
        saved = await http.put(
            _contents(path), json={"type": "file", "format": "text", "content": content}
        )
        saved.raise_for_status()
        return saved.json()


async def read_notebook(target: JupyterTarget, path: str) -> dict[str, Any]:
    async with jupyter_http(target) as http:
        found = await http.get(_contents(path), params={"type": "notebook", "content": "1"})
        found.raise_for_status()
        return found.json()["content"]


async def write_notebook(target: JupyterTarget, path: str, notebook: dict[str, Any]) -> None:
    async with jupyter_http(target) as http:
        await _ensure_folders(http, path)
        saved = await http.put(_contents(path), json={"type": "notebook", "content": notebook})
        saved.raise_for_status()
