"""Workspace activity as metadata: file actions, kernel actions, cell runs and changing requests.

Each is one JSON line on stdout in CRAFT's log format, so ``kubectl logs`` shows it beside
CRAFT's call log, joined by ``user_id`` (JupyterHub names each server after the CRAFT user
ID). Lines hold who, which path, sizes, counts and timings: never code, outputs or file
content. Everything is observed in the server process, so code in a kernel can't write here.
"""

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONTENTS = "https://events.jupyter.org/jupyter_server/contents_service/v1"
KERNELS = "https://events.jupyter.org/jupyter_server/kernel_actions/v1"
FILE_ACTIONS = {"save", "rename", "copy", "delete"}  # reads ("get") aren't logged
READS = {"GET", "HEAD", "OPTIONS"}
# The IOPub messages whose content a cell run reads; outputs, which can be large, stay unparsed.
READ_CONTENT = {"status", "execute_input"}


def _jupyter_server_extension_points():
    return [{"module": "craft_events"}]


def write(kind: str, **fields: Any) -> None:
    line = {
        "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "level": "info",
        "logger": "craft.workspace",
        "event": "workspace",
        "kind": kind,
        "origin": "workspace",
        "user_id": os.environ.get("JUPYTERHUB_USER") or None,
        **fields,
    }
    sys.stdout.write(json.dumps(line, separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()


def _size(path: str) -> int | None:
    try:
        return (Path(os.environ.get("USER_ROOT", "/workspace")) / path).stat().st_size
    except OSError:
        return None


async def on_file(logger: Any, schema_id: str, data: dict[str, Any]) -> None:
    action = data["action"]
    if action not in FILE_ACTIONS:
        return
    fields: dict[str, Any] = {"path": data["path"]}
    if "source_path" in data:
        fields["source_path"] = data["source_path"]
    if action == "save":
        fields["bytes"] = _size(data["path"])
    write("file." + action, **fields)


class Cells:
    """Times each cell run from a kernel's IOPub messages, keyed by the request's message ID.

    A run is the kernel going busy for an ``execute_request`` that echoes its input; silent
    requests (completions, widget state) echo none and aren't logged.
    """

    def __init__(self, kernel_id: str, path_of) -> None:
        self.kernel_id = kernel_id
        self.path_of = path_of  # async kernel ID -> notebook path, or None
        self.runs: dict[str, dict[str, Any]] = {}
        self.writes: set[asyncio.Future] = set()  # the loop keeps only weak references

    def feed(self, session: Any, parts: list) -> None:
        """One IOPub message from the kernel, parsed only as far as a cell run needs."""
        _, fed = session.feed_identities(parts)
        msg = session.deserialize(fed, content=False)
        msg["content"] = (
            session.unpack(msg["content"]) if msg["header"]["msg_type"] in READ_CONTENT else {}
        )
        self.message(msg)

    def message(self, msg: dict[str, Any]) -> None:
        parent = msg.get("parent_header") or {}
        if parent.get("msg_type") != "execute_request":
            return
        request, kind = str(parent.get("msg_id")), msg["header"]["msg_type"]
        content = msg.get("content") or {}
        if kind == "status" and content.get("execution_state") == "busy":
            self.runs[request] = {"started": time.monotonic(), "outcome": "ok"}
        elif request not in self.runs:
            return
        elif kind == "execute_input":
            self.runs[request]["count"] = content.get("execution_count")
        elif kind == "error":
            self.runs[request]["outcome"] = "error"
        elif kind == "status" and content.get("execution_state") == "idle":
            run = self.runs.pop(request)
            if "count" in run:
                write = asyncio.ensure_future(self._write(run))
                self.writes.add(write)
                write.add_done_callback(self.writes.discard)

    async def _write(self, run: dict[str, Any]) -> None:
        write(
            "cell.execute",
            path=await self.path_of(self.kernel_id),
            kernel_id=self.kernel_id,
            execution_count=run["count"],
            duration_ms=round((time.monotonic() - run["started"]) * 1000),
            outcome=run["outcome"],
        )


def _load_jupyter_server_extension(serverapp) -> None:
    from jupyter_client.session import Session

    kernels = serverapp.kernel_manager
    streams: dict[str, Any] = {}

    async def path_of(kernel_id: str) -> str | None:
        for session in await serverapp.session_manager.list_sessions():
            if session.get("kernel", {}).get("id") == kernel_id:
                return session.get("path")
        return None

    # Ride on Jupyter Server's own activity watch: it starts once a kernel is registered and
    # stops before a shutdown or restart, with the same IOPub subscription.
    start_watching, stop_watching = kernels.start_watching_activity, kernels.stop_watching_activity

    def start_watching_activity(kernel_id: str) -> None:
        start_watching(kernel_id)
        kernel = kernels.get_kernel(kernel_id)
        session = Session(config=kernel.session.config, key=kernel.session.key)
        cells = Cells(kernel_id, path_of)

        streams[kernel_id] = kernel.connect_iopub()
        streams[kernel_id].on_recv(lambda parts: cells.feed(session, parts))

    def stop_watching_activity(kernel_id: str) -> None:
        stop_watching(kernel_id)
        stream = streams.pop(kernel_id, None)
        if stream is not None and not stream.socket.closed:
            stream.close()

    kernels.start_watching_activity = start_watching_activity
    kernels.stop_watching_activity = stop_watching_activity

    async def on_kernel(logger: Any, schema_id: str, data: dict[str, Any]) -> None:
        write("kernel." + data["action"], kernel_id=data.get("kernel_id"), outcome=data["status"])

    serverapp.event_logger.add_listener(schema_id=CONTENTS, listener=on_file)
    serverapp.event_logger.add_listener(schema_id=KERNELS, listener=on_kernel)

    # Requests that change something, and failures; reads and polling would drown them.
    jupyter_log = serverapp.web_app.settings["log_function"]

    def log_request(handler) -> None:
        jupyter_log(handler)
        request, status = handler.request, handler.get_status()
        if request.method in READS and status < 400:
            return
        write(
            "request",
            method=request.method,
            path=request.path,  # never the query string, which can carry a token
            status=status,
            duration_ms=round(request.request_time() * 1000),
        )

    serverapp.web_app.settings["log_function"] = log_request
