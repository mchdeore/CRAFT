"""Owned Docker transport for the fixed managed kernel image."""

import json
import math
import os
import select
import subprocess
import threading
import time
import uuid

from craft_workspace.files import MAX_FILE
from craft_workspace.kernel_worker import OUTPUT_BYTES, REQUEST_BYTES, WorkerError

_INTERNAL_INPUT_BYTES = 10 * 1024 * 1024
_INTERNAL_OUTPUT_BYTES = MAX_FILE * 4 // 3 + 65536


class DockerLauncher:
    def __init__(self, image: str = "craft-python-workspace:local-pilot"):
        self.image = image

    def open(self, timeout: float = 20.0):
        transport = DockerTransport(image=self.image)
        try:
            transport.open(timeout=timeout)
            return transport
        except Exception:
            transport.close()
            raise


class DockerTransport:
    def __init__(self, image: str):
        self.image = image
        self.container_name = f"craft-workspace-{uuid.uuid4().hex}"
        self.process = None
        self._stdout_buffer = bytearray()
        self._request_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False
        self._healthy = False

    @property
    def healthy(self) -> bool:
        return self._healthy and not self._closed

    def open(self, timeout: float = 20.0):
        with self._close_lock:
            if self.process is not None:
                raise WorkerError("already_open")
            self.process = subprocess.Popen(
                self._docker_run_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        try:
            self.request("open", wait_seconds=timeout)
            self._healthy = True
            return self
        except Exception:
            self._healthy = False
            raise

    def request(self, action: str, *, wait_seconds=None, **fields):
        if action not in {"open", "execute", "close"} or {"id", "action"}.intersection(fields):
            raise WorkerError("invalid_request")
        cell_timeout = fields.get("timeout", 60.0)
        timeout = (
            wait_seconds
            if wait_seconds is not None
            else cell_timeout + 8
            if action == "execute"
            else 20
        )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 75
        ):
            raise WorkerError("invalid_timeout")
        if action == "execute" and not self.healthy:
            raise WorkerError("unhealthy")
        if not self._request_lock.acquire(blocking=False):
            raise WorkerError("busy")
        try:
            if self._closed or self.process is None or self.process.stdin is None:
                raise WorkerError("closed")
            if self.process.poll() is not None:
                self._healthy = False
                raise WorkerError("unavailable")
            identifier = uuid.uuid4().hex
            payload = {"id": identifier, "action": action, **fields}
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > REQUEST_BYTES:
                raise WorkerError("request_too_large")
            deadline = time.monotonic() + float(timeout)
            try:
                self._write_with_deadline(self.process.stdin, encoded + b"\n", deadline)
            except TimeoutError:
                self._healthy = False
                raise WorkerError("request_timeout") from None
            except Exception:
                self._healthy = False
                raise WorkerError("unavailable") from None
            while True:
                reply = self._read_json_line(deadline=deadline, limit=REQUEST_BYTES)
                if reply.get("id") != identifier:
                    self._healthy = False
                    raise WorkerError("invalid_protocol")
                if reply.get("ok") is True:
                    if action == "execute":
                        self._healthy = reply.get("healthy") is True
                    return reply
                if reply.get("ok") is False:
                    error = reply.get("error")
                    if not isinstance(error, str) or not error:
                        error = "request_failed"
                    if error == "execution_timeout":
                        self._healthy = bool(reply.get("healthy"))
                    raise WorkerError("worker_request_failed")
                self._healthy = False
                raise WorkerError("invalid_protocol")
        except TimeoutError:
            self._healthy = False
            raise WorkerError("request_timeout") from None
        finally:
            self._request_lock.release()

    def exec_python(
        self,
        script,
        args=(),
        input_bytes=b"",
        max_output=OUTPUT_BYTES,
        timeout: float = 10,
    ):
        if self._closed or self.process is None or self.process.poll() is not None:
            raise WorkerError("closed")
        if not isinstance(script, str):
            raise WorkerError("invalid_script")
        if not isinstance(input_bytes, (bytes, bytearray)):
            raise WorkerError("invalid_input")
        if len(input_bytes) > _INTERNAL_INPUT_BYTES:
            raise WorkerError("invalid_input")
        if (
            isinstance(max_output, bool)
            or not isinstance(max_output, int)
            or max_output < 0
            or max_output > _INTERNAL_OUTPUT_BYTES
        ):
            raise WorkerError("invalid_max_output")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > 75
        ):
            raise WorkerError("invalid_timeout")

        command = [
            "docker",
            "exec",
            "-i",
            "--user",
            "10001:10001",
            self.container_name,
            "python",
            "-I",
            "-c",
            script,
            *[str(arg) for arg in args],
        ]
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        deadline = time.monotonic() + float(timeout)
        try:
            if proc.stdin is None:
                raise WorkerError("exec_failed")
            self._write_with_deadline(proc.stdin, bytes(input_bytes), deadline)
            self._close_pipe(proc.stdin)
            proc.stdin = None
            return self._collect_process_output(
                proc,
                max_output=max_output,
                deadline=deadline,
            )
        except TimeoutError:
            raise WorkerError("exec_timeout") from None
        except WorkerError:
            raise
        except Exception:
            raise WorkerError("exec_failed") from None
        finally:
            self._cleanup_subprocess(proc)

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            try:
                if self.process is not None and self.process.poll() is None and self.process.stdin:
                    try:
                        self.request("close", wait_seconds=2.0)
                    except Exception:
                        pass
            finally:
                proc = self.process
                self.process = None
                self._closed = True
                self._healthy = False
                if proc is not None:
                    self._cleanup_subprocess(proc)
                subprocess.run(
                    ["docker", "rm", "-f", self.container_name],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )

    def _docker_run_command(self):
        return [
            "docker",
            "run",
            "--name",
            self.container_name,
            "--rm",
            "-i",
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--cpus",
            "1",
            "--memory",
            "2g",
            "--pids-limit",
            "128",
            "--user",
            "10001:10001",
            "--workdir",
            "/workspace",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=256m,uid=10001,gid=10001",
            "--tmpfs",
            "/workspace:rw,nosuid,nodev,size=100m,uid=10001,gid=10001",
            "--env",
            "HOME=/tmp/home",
            "--env",
            "IPYTHONDIR=/tmp/ipython",
            "--env",
            "JUPYTER_RUNTIME_DIR=/tmp/jupyter-runtime",
            "--env",
            "OPENBLAS_NUM_THREADS=1",
            self.image,
            "python",
            "-u",
            "-m",
            "craft_workspace.kernel_worker",
        ]

    def _read_json_line(self, deadline: float, limit: int):
        line = self._read_line_bytes(deadline=deadline, limit=limit)
        try:
            value = json.loads(line.decode("utf-8"))
        except Exception:
            self._healthy = False
            raise WorkerError("invalid_protocol") from None
        if not isinstance(value, dict):
            self._healthy = False
            raise WorkerError("invalid_protocol")
        return value

    def _read_line_bytes(self, deadline: float, limit: int):
        if self.process is None or self.process.stdout is None:
            raise WorkerError("closed")
        fd = self.process.stdout.fileno()
        while True:
            newline = self._stdout_buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._stdout_buffer[:newline])
                del self._stdout_buffer[: newline + 1]
                if len(line) > limit:
                    self._healthy = False
                    raise WorkerError("response_too_large")
                return line
            if len(self._stdout_buffer) > limit:
                self._healthy = False
                raise WorkerError("response_too_large")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            ready, _, _ = select.select([fd], [], [], min(0.1, remaining))
            if not ready:
                if self.process.poll() is not None:
                    self._healthy = False
                    raise WorkerError("unavailable")
                continue
            chunk = os.read(fd, min(4096, limit - len(self._stdout_buffer) + 1))
            if not chunk:
                self._healthy = False
                raise WorkerError("unavailable")
            self._stdout_buffer.extend(chunk)

    def _collect_process_output(self, proc, max_output: int, deadline: float):
        stdout = bytearray()
        stderr = bytearray()
        truncated = False
        fds = {}
        if proc.stdout is not None:
            fds[proc.stdout.fileno()] = (proc.stdout, stdout)
        if proc.stderr is not None:
            fds[proc.stderr.fileno()] = (proc.stderr, stderr)
        while fds:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            ready, _, _ = select.select(list(fds), [], [], min(0.1, remaining))
            if not ready:
                continue
            for fd in ready:
                _, target = fds[fd]
                chunk = os.read(fd, 4096)
                if not chunk:
                    del fds[fd]
                    continue
                allowed = max(0, max_output - (len(stdout) + len(stderr)))
                if allowed:
                    target.extend(chunk[:allowed])
                if len(chunk) > allowed:
                    truncated = True
        returncode = proc.wait(timeout=1)
        return {
            "returncode": returncode,
            "stdout": bytes(stdout),
            "stderr": bytes(stderr),
            "truncated": truncated,
        }

    def _write_with_deadline(self, stream, data: bytes, deadline: float):
        if stream is None:
            raise BrokenPipeError
        fd = stream.fileno()
        previous = os.get_blocking(fd)
        os.set_blocking(fd, False)
        pending = memoryview(data)
        try:
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                _, writable, _ = select.select([], [fd], [], min(0.1, remaining))
                if not writable:
                    continue
                try:
                    wrote = os.write(fd, pending[:65536])
                except BlockingIOError:
                    continue
                if wrote <= 0:
                    raise BrokenPipeError
                pending = pending[wrote:]
        finally:
            os.set_blocking(fd, previous)

    def _cleanup_subprocess(self, proc):
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            self._close_pipe(stream)
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=1)
        except Exception:
            pass

    @staticmethod
    def _close_pipe(stream):
        if stream is None:
            return
        try:
            stream.close()
        except Exception:
            pass
