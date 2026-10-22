"""Private Unix socket and lifecycle delegated to supervisord."""

import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from http.client import HTTPException
from pathlib import Path
from typing import Any
from uuid import uuid4
from xmlrpc.client import Error, ServerProxy

from supervisor.xmlrpc import SupervisorTransport, UnixStreamHTTPConnection

from app.processes.core import State
from app.processes.specs import LifecycleError, ProcessSpec, Spec, validate


@contextmanager
def safe_rpc():
    try:
        yield
    except (Error, OSError, HTTPException):
        raise LifecycleError("process_backend_failed") from None


class BoundedUnixConnection(UnixStreamHTTPConnection):
    socketfile: str

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(3)
        self.sock.connect(self.socketfile)


class BoundedTransport(SupervisorTransport):
    def __init__(self, path: Path):
        super().__init__(None, None, f"unix://{path}")
        self._get_connection = lambda: self.connection_for(path)

    def request(self, host, handler, request_body, verbose=False):
        try:
            return super().request(host, handler, request_body, verbose)
        except (Error, OSError, HTTPException):
            self.close()
            raise

    @staticmethod
    def connection_for(path: Path):
        connection = BoundedUnixConnection("localhost")
        connection.socketfile = str(path)
        return connection


class SupervisorBackend:
    def __init__(self, root: Path):
        if os.name != "posix":
            raise LifecycleError("platform_unsupported")
        if any(character in str(root) for character in "\n\r;#"):
            raise LifecycleError("unsafe_process_root")
        root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="craft-processes-", dir=root.resolve()))
        self.owner = uuid4().hex
        (self.root / "owner").write_text(self.owner)
        (self.root / "owner").chmod(0o600)
        (self.root / "programs").mkdir(mode=0o700)
        self.specs: dict[str, ProcessSpec] = {}
        self.ipc = Path(tempfile.mkdtemp(prefix="craft-ipc-", dir="/tmp"))
        self.socket = self.ipc / "control.sock"
        self.config = self.root / "supervisord.ini"
        self.config.write_text(self.daemon_config())
        self.config.chmod(0o600)
        self.daemon = subprocess.Popen(
            [sys.executable, "-m", "supervisor.supervisord", "-n", "-c", str(self.config)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": os.defpath},
            start_new_session=True,
        )
        self.transport = BoundedTransport(self.socket)
        self.rpc: Any = ServerProxy("http://127.0.0.1/RPC2", transport=self.transport)
        try:
            self.connect()
        except Exception:
            self.daemon.terminate()
            try:
                self.daemon.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.daemon.kill()
                self.daemon.wait(timeout=3)
            self.transport.close()
            shutil.rmtree(self.root)
            shutil.rmtree(self.ipc)
            raise LifecycleError("process_backend_unavailable") from None

    def daemon_config(self) -> str:
        root = str(self.root).replace("%", "%%")
        return f"""[unix_http_server]
file={self.socket}
chmod=0600
[supervisord]
nodaemon=true
logfile=/dev/null
pidfile={root}/supervisord.pid
childlogdir={root}
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix://{self.socket}
[include]
files={root}/programs/*.ini
"""

    def connect(self) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.daemon.poll() is None:
            try:
                if self.rpc.supervisor.getPID() == self.daemon.pid:
                    return
            except (OSError, Error, HTTPException):
                pass
            time.sleep(0.02)
        raise LifecycleError("process_backend_unavailable")

    def owned(self, spec: Spec) -> ProcessSpec:
        if not isinstance(spec, ProcessSpec) or self.specs.get(spec.id) != spec:
            raise LifecycleError("process_ownership_mismatch")
        marker = self.root / "owner"
        if marker.is_symlink() or not marker.is_file() or self.root.is_symlink():
            raise LifecycleError("process_ownership_mismatch")
        if (self.root / "owner").read_text() != self.owner:
            raise LifecycleError("process_ownership_mismatch")
        if self.daemon.poll() is not None:
            raise LifecycleError("process_backend_unavailable")
        return spec

    def program_config(self, spec: ProcessSpec) -> str:
        command = shlex.join(
            (
                sys.executable,
                "-m",
                "app.processes.exec",
                str(self.root / "programs" / f"{spec.id}.json"),
            )
        ).replace("%", "%%")
        restart = {"never": "false", "on-failure": "unexpected", "always": "true"}[spec.restart]
        return f"""[program:craft-{spec.id}]
command={command}
priority={spec.priority}
autostart=false
autorestart={restart}
startsecs=1
startretries={spec.retries}
exitcodes=0
stopsignal=TERM
stopwaitsecs={spec.stop_seconds}
stopasgroup=true
killasgroup=true
stdout_logfile=/dev/null
stderr_logfile=/dev/null
"""

    def register(self, spec: Spec) -> None:
        validate(spec)
        if not isinstance(spec, ProcessSpec) or spec.id in self.specs:
            raise LifecycleError("process_ownership_mismatch")
        path = self.root / "programs" / f"{spec.id}.ini"
        arguments = path.with_suffix(".json")
        arguments.write_text(json.dumps(spec.command))
        arguments.chmod(0o600)
        path.write_text(self.program_config(spec))
        path.chmod(0o600)
        with safe_rpc():
            self.rpc.supervisor.reloadConfig()
            self.rpc.supervisor.addProcessGroup(f"craft-{spec.id}")
        self.specs[spec.id] = spec

    def start(self, spec: Spec) -> None:
        self.owned(spec)
        with safe_rpc():
            self.rpc.supervisor.startProcess(f"craft-{spec.id}", False)

    def stop(self, spec: Spec) -> None:
        self.owned(spec)
        with safe_rpc():
            info = self.rpc.supervisor.getProcessInfo(f"craft-{spec.id}")
            if info["statename"] in {"STARTING", "RUNNING", "BACKOFF"}:
                self.rpc.supervisor.stopProcess(f"craft-{spec.id}", False)

    def state(self, spec: Spec) -> State:
        self.owned(spec)
        with safe_rpc():
            name = self.rpc.supervisor.getProcessInfo(f"craft-{spec.id}")["statename"]
        states: dict[str, State] = {
            "STOPPED": "stopped",
            "STARTING": "starting",
            "RUNNING": "running",
            "STOPPING": "stopping",
            "BACKOFF": "starting",
            "EXITED": "exited",
            "FATAL": "failed",
        }
        return states.get(name, "failed")

    def remove(self, spec: Spec) -> None:
        self.owned(spec)
        with safe_rpc():
            self.rpc.supervisor.removeProcessGroup(f"craft-{spec.id}")
        (self.root / "programs" / f"{spec.id}.ini").unlink()
        (self.root / "programs" / f"{spec.id}.json").unlink()
        del self.specs[spec.id]

    def close(self) -> None:
        if not self.root.exists():
            return
        if (self.root / "owner").read_text() != self.owner:
            raise LifecycleError("process_ownership_mismatch")
        with safe_rpc():
            self.rpc.supervisor.shutdown()
        try:
            self.daemon.wait(timeout=20)
        except subprocess.TimeoutExpired:
            raise LifecycleError("process_cleanup_incomplete") from None
        self.transport.close()
        shutil.rmtree(self.root)
        shutil.rmtree(self.ipc)
