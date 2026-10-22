"""Trusted local controller; application authentication and export approval attach later."""

import base64
import csv
import hashlib
import io
import json
import threading
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from craft_workspace.files import MAX_FILE, parts
from craft_workspace.kernel_worker import WorkerError
from craft_workspace.transport import DockerLauncher

FILE_SCRIPT = "import sys; from craft_workspace.files import dispatch; dispatch(*sys.argv[1:])"


class Transport(Protocol):
    def request(self, action: str, **fields) -> dict[str, Any]: ...
    def exec_python(
        self, script: str, args=(), input_bytes=b"", max_output=65536, timeout=10
    ) -> dict[str, Any]: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class Artifact:
    name: str
    size: int


@dataclass(frozen=True)
class ExecutionResult:
    stdout: str
    stderr: str
    text: str
    error: str | None
    timed_out: bool
    truncated: bool
    artifacts: list[Artifact]


@dataclass(frozen=True)
class CloseResult:
    session: str
    bundle: bytes
    artifacts: list[Artifact]
    sha256: str


@dataclass
class Session:
    transport: Transport
    lock: threading.Lock = field(default_factory=threading.Lock)


class WorkspaceManager:
    def __init__(self, launcher=None):
        self.launcher = launcher or DockerLauncher()
        self.sessions: dict[str, Session] = {}
        self.closed: dict[str, CloseResult] = {}
        self.lock = threading.Lock()

    def open(self):
        transport = self.launcher.open()
        identifier = uuid.uuid4().hex
        with self.lock:
            self.sessions[identifier] = Session(transport)
        return identifier

    @contextmanager
    def owned(self, identifier):
        with self.lock:
            session = self.sessions.get(identifier)
        if session is None:
            raise WorkerError("session_closed")
        if not session.lock.acquire(blocking=False):
            raise WorkerError("busy")
        try:
            yield session
        finally:
            session.lock.release()

    @staticmethod
    def file_operation(session, operation, name="", content=b""):
        response = session.transport.exec_python(
            FILE_SCRIPT,
            args=(operation, name),
            input_bytes=content,
            max_output=MAX_FILE * 4 // 3 + 65536,
            timeout=15,
        )
        if response["returncode"] != 0 or response["truncated"]:
            raise WorkerError("file_operation_failed")
        try:
            result = json.loads(response["stdout"])
            if result["ok"] is not True:
                raise WorkerError(result.get("error", "file_operation_failed"))
            return result["result"]
        except (ValueError, KeyError, TypeError):
            raise WorkerError("invalid_file_response") from None

    @classmethod
    def artifacts(cls, session):
        return [Artifact(**row) for row in cls.file_operation(session, "list")]

    def put_csv(self, identifier, name, content):
        parts(name)
        if (
            not isinstance(content, bytes)
            or len(content) > 10 * 1024 * 1024
            or not name.endswith(".csv")
        ):
            raise WorkerError("invalid_csv")
        try:
            rows = csv.reader(io.StringIO(content.decode("utf-8-sig")), strict=True)
            header = next(rows)
            if not header or any(not cell.strip() for cell in header):
                raise ValueError()
            if any(len(row) != len(header) for row in rows):
                raise ValueError()
        except (UnicodeDecodeError, ValueError, csv.Error, StopIteration):
            raise WorkerError("invalid_csv") from None
        with self.owned(identifier) as session:
            self.file_operation(session, "put", name, content)
            return Artifact(name, len(content))

    def execute(self, identifier, code, timeout=60):
        with self.owned(identifier) as session:
            response = session.transport.request("execute", code=code, timeout=timeout)
            return ExecutionResult(
                response["stdout"],
                response["stderr"],
                response["text"],
                response["error"],
                response["timedOut"],
                response["truncated"],
                self.artifacts(session),
            )

    def list_files(self, identifier):
        with self.owned(identifier) as session:
            return self.artifacts(session)

    def read_file(self, identifier, name):
        parts(name)
        with self.owned(identifier) as session:
            result = self.file_operation(session, "read", name)
            content = base64.b64decode(result["content"], validate=True)
            if (
                len(content) != result["size"]
                or hashlib.sha256(content).hexdigest() != result["sha256"]
            ):
                raise WorkerError("file_changed")
            return content

    def close(self, identifier):
        with self.lock:
            previous = self.closed.get(identifier)
        if previous is not None:
            return previous
        with self.owned(identifier) as session:
            artifacts = self.artifacts(session)
            if sum(artifact.size for artifact in artifacts) > MAX_FILE:
                raise WorkerError("bundle_too_large")
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
                for artifact in artifacts:
                    result = self.file_operation(session, "read", artifact.name)
                    content = base64.b64decode(result["content"], validate=True)
                    if (
                        len(content) != artifact.size
                        or hashlib.sha256(content).hexdigest() != result["sha256"]
                    ):
                        raise WorkerError("file_changed")
                    archive.writestr(artifact.name, content)
            bundle = output.getvalue()
            if len(bundle) > MAX_FILE:
                raise WorkerError("bundle_too_large")
            result = CloseResult(identifier, bundle, artifacts, hashlib.sha256(bundle).hexdigest())
            session.transport.close()
            with self.lock:
                self.closed[identifier] = result
                self.sessions.pop(identifier, None)
            return result
