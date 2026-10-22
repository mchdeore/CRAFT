"""JSON-line control for one persistent kernel inside a restricted container."""

import json
import math
import queue
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from jupyter_client.manager import KernelManager

OUTPUT_BYTES = 65536
REQUEST_BYTES = 1024 * 1024
MAX_SECONDS = 60.0


class WorkerError(Exception):
    pass


@dataclass
class TextBudget:
    remaining: int = OUTPUT_BYTES
    truncated: bool = False

    def take(self, text) -> str:
        encoded = str(text).encode("utf-8", errors="replace")
        value = encoded[: self.remaining].decode("utf-8", errors="ignore")
        self.remaining -= len(value.encode())
        self.truncated |= len(encoded) > len(value.encode())
        return value


class KernelWorker:
    def __init__(self, root="/workspace"):
        self.root = Path(root)
        self.manager = self.client = self.temporary = None
        self.healthy = False
        self.lock = threading.Lock()

    @contextmanager
    def exclusive(self):
        if not self.lock.acquire(blocking=False):
            raise WorkerError("busy")
        try:
            yield
        finally:
            self.lock.release()

    def open(self):
        with self.exclusive():
            if self.manager is not None:
                raise WorkerError("already_open")
            self.root.mkdir(parents=True, exist_ok=True)
            self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
            self.manager = KernelManager(
                connection_file=str(Path(self.temporary.name) / "kernel.json")
            )
            try:
                self.manager.start_kernel(
                    cwd=str(self.root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self.client = self.manager.blocking_client()
                self.client.start_channels()
                self.client.wait_for_ready(timeout=15)
                self.healthy = True
            except Exception:
                self.shutdown()
                raise WorkerError("startup_failed") from None
            return {"status": "open"}

    def execute(self, code, timeout=MAX_SECONDS):
        with self.exclusive():
            if self.client is None or not self.healthy:
                raise WorkerError("kernel_unavailable")
            if not isinstance(code, str) or len(code.encode()) > REQUEST_BYTES:
                raise WorkerError("invalid_code")
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout)
                or not 0 < timeout <= MAX_SECONDS
            ):
                raise WorkerError("invalid_timeout")
            identifier = self.client.execute(code, allow_stdin=False, stop_on_error=True)
            deadline = time.monotonic() + timeout
            budget = TextBudget()
            result = {
                "status": "ok",
                "stdout": "",
                "stderr": "",
                "text": "",
                "error": None,
                "timedOut": False,
            }
            idle = False
            while time.monotonic() < deadline:
                try:
                    message = self.client.get_iopub_msg(
                        timeout=min(0.1, max(0, deadline - time.monotonic()))
                    )
                except queue.Empty:
                    continue
                if message.get("parent_header", {}).get("msg_id") != identifier:
                    continue
                kind, content = message["msg_type"], message["content"]
                if kind == "status" and content.get("execution_state") == "idle":
                    idle = True
                    break
                if kind == "stream":
                    channel = "stderr" if content.get("name") == "stderr" else "stdout"
                    result[channel] += budget.take(content.get("text", ""))
                elif kind in {"execute_result", "display_data"}:
                    result["text"] += budget.take(content.get("data", {}).get("text/plain", ""))
                elif kind == "error":
                    result["status"] = "error"
                    result["error"] = budget.take(
                        content.get("ename", "Error") + ": " + content.get("evalue", "")
                    )
            if not idle:
                result["status"], result["timedOut"] = "error", True
                result["error"] = "execution_timeout"
                self.healthy = self.interrupt(identifier)
            else:
                try:
                    while time.monotonic() < deadline:
                        reply = self.client.get_shell_msg(
                            timeout=min(0.1, max(0, deadline - time.monotonic()))
                        )
                        if reply.get("parent_header", {}).get("msg_id") == identifier:
                            if reply.get("content", {}).get("status") != "ok":
                                result["status"] = "error"
                            break
                except queue.Empty:
                    pass
            result["truncated"] = budget.truncated
            result["healthy"] = self.healthy
            return result

    def interrupt(self, identifier):
        assert self.manager is not None and self.client is not None
        try:
            self.manager.interrupt_kernel()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    message = self.client.get_iopub_msg(timeout=0.1)
                except queue.Empty:
                    continue
                if (
                    message.get("parent_header", {}).get("msg_id") == identifier
                    and message["msg_type"] == "status"
                    and message["content"].get("execution_state") == "idle"
                ):
                    return True
        except Exception:
            pass
        return False

    def shutdown(self):
        try:
            if self.manager is not None:
                self.manager.shutdown_kernel(now=True)
        finally:
            try:
                if self.client is not None:
                    self.client.stop_channels()
            finally:
                if self.temporary is not None:
                    self.temporary.cleanup()
                self.client = self.manager = self.temporary = None
                self.healthy = False

    def close(self):
        with self.exclusive():
            self.shutdown()
            return {"status": "closed"}


def handle(worker, raw):
    identifier = None
    try:
        if len(raw.encode()) > REQUEST_BYTES:
            raise WorkerError("request_too_large")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise WorkerError("invalid_request")
        identifier = request.get("id")
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 200:
            raise WorkerError("invalid_id")
        action = request.get("action")
        if action == "open":
            result = worker.open()
        elif action == "execute":
            result = worker.execute(request.get("code"), request.get("timeout", MAX_SECONDS))
        elif action == "close":
            result = worker.close()
        else:
            raise WorkerError("invalid_action")
        return {"id": identifier, "ok": True, **result}
    except WorkerError as error:
        return {"id": identifier, "ok": False, "error": str(error)}
    except Exception:
        return {"id": identifier, "ok": False, "error": "worker_failed"}


def main():
    worker = KernelWorker()
    try:
        while raw := sys.stdin.readline(REQUEST_BYTES + 1):
            oversized = len(raw.encode()) > REQUEST_BYTES or not raw.endswith("\n")
            if oversized:
                while raw and not raw.endswith("\n"):
                    raw = sys.stdin.readline(REQUEST_BYTES + 1)
                response = {"id": None, "ok": False, "error": "request_too_large"}
            elif not raw.strip():
                continue
            else:
                response = handle(worker, raw)
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    finally:
        worker.close()


if __name__ == "__main__":
    main()
