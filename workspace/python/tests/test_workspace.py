import io
import zipfile

import pytest

from craft_workspace.files import FileError, parts
from craft_workspace.kernel_worker import WorkerError
from craft_workspace.manager import WorkspaceManager
from craft_workspace.transport import DockerTransport


@pytest.fixture
def manager():
    result = WorkspaceManager()
    yield result
    for identifier, session in list(result.sessions.items()):
        session.transport.close()
        result.sessions.pop(identifier, None)


@pytest.mark.slow
@pytest.mark.docker
def test_real_csv_persistent_execution_and_export(manager):
    session = manager.open()
    manager.put_csv(session, "inputs/example.csv", b"value\n41\n")
    first = manager.execute(
        session,
        "from pathlib import Path\n"
        "x=int(Path('inputs/example.csv').read_text().splitlines()[1]); print(x)",
    )
    assert first.stdout.strip() == "41"
    second = manager.execute(session, "print(x+1); Path('result.txt').write_text(str(x+1))")
    assert second.stdout.strip() == "42" and manager.read_file(session, "result.txt") == b"42"
    closed = manager.close(session)
    with zipfile.ZipFile(io.BytesIO(closed.bundle)) as archive:
        assert archive.read("result.txt") == b"42"
        assert archive.read("inputs/example.csv") == b"value\n41\n"
    assert manager.close(session) is closed
    fresh = manager.open()
    missing = manager.execute(fresh, "print(x)")
    assert missing.error and "NameError" in missing.error
    manager.close(fresh)


@pytest.mark.parametrize(
    "name", ["/etc/passwd", "../escape.csv", "a/../escape.csv", "a\\b.csv", ".env", "a//b.csv"]
)
def test_invalid_paths_never_launch_a_workspace(name):
    with pytest.raises(FileError, match="invalid_name"):
        parts(name)


@pytest.mark.slow
@pytest.mark.docker
def test_timeout_bounded_output_and_symlink_refusal(manager):
    session = manager.open()
    result = manager.execute(session, "import time; time.sleep(5)", timeout=0.1)
    assert result.timed_out
    output = manager.execute(session, "print('a'*100000)")
    assert output.truncated and len(output.stdout.encode()) <= 65536
    with pytest.raises(WorkerError, match="unsafe_files"):
        manager.execute(
            session, "from pathlib import Path; Path('link.txt').symlink_to('/etc/passwd')"
        )
    with pytest.raises(WorkerError):
        manager.read_file(session, "link.txt")
    with pytest.raises(WorkerError):
        manager.close(session)
    assert session in manager.sessions
    manager.execute(session, "Path('link.txt').unlink()")
    manager.close(session)


@pytest.mark.slow
@pytest.mark.docker
def test_close_limit_preserves_individual_retrieval(manager):
    session = manager.open()
    manager.execute(
        session,
        "from pathlib import Path\n"
        "Path('large.bin').write_bytes(b'x'*(20*1024*1024))\n"
        "Path('other.bin').write_bytes(b'y'*(6*1024*1024))",
    )
    with pytest.raises(WorkerError, match="bundle_too_large"):
        manager.close(session)
    assert len(manager.read_file(session, "other.bin")) == 6 * 1024 * 1024
    assert session in manager.sessions
    manager.execute(session, "Path('large.bin').unlink()")
    manager.close(session)


@pytest.mark.slow
@pytest.mark.docker
def test_concurrent_execution_and_invalid_csv_refused(manager):
    session = manager.open()
    for content in [b"\xff", b"a,b\n1\n", b"", b"a\n" + b"x" * (10 * 1024 * 1024)]:
        with pytest.raises(WorkerError, match="invalid_csv"):
            manager.put_csv(session, "input.csv", content)
    acquired = manager.sessions[session].lock
    acquired.acquire()
    try:
        with pytest.raises(WorkerError, match="busy"):
            manager.execute(session, "1+1")
    finally:
        acquired.release()
    manager.close(session)


def test_fake_controller_exports_before_shutdown():
    import base64
    import hashlib
    import json

    events = []

    class FakeTransport:
        def request(self, action, **fields):
            events.append(action)
            return {
                "stdout": "synthetic",
                "stderr": "",
                "text": "",
                "error": None,
                "timedOut": False,
                "truncated": False,
            }

        def exec_python(self, script, args=(), **fields):
            operation, name = args
            events.append(operation)
            value = b"synthetic"
            result = (
                [{"name": "result.txt", "size": len(value)}]
                if operation == "list"
                else {
                    "name": name,
                    "size": len(value),
                    "sha256": hashlib.sha256(value).hexdigest(),
                    "content": base64.b64encode(value).decode(),
                }
            )
            return {
                "returncode": 0,
                "truncated": False,
                "stdout": json.dumps({"ok": True, "result": result}).encode(),
            }

        def close(self):
            events.append("shutdown")

    class FakeLauncher:
        def open(self):
            return FakeTransport()

    manager = WorkspaceManager(FakeLauncher())
    session = manager.open()
    result = manager.close(session)
    assert events == ["list", "read", "shutdown"]
    with zipfile.ZipFile(io.BytesIO(result.bundle)) as archive:
        assert archive.read("result.txt") == b"synthetic"


@pytest.mark.slow
@pytest.mark.docker
def test_container_failure_has_safe_error_and_owned_cleanup(manager):
    import subprocess

    session = manager.open()
    transport = manager.sessions[session].transport
    subprocess.run(["docker", "kill", transport.container_name], capture_output=True, check=True)
    with pytest.raises(WorkerError):
        manager.execute(session, "print(1)", timeout=0.2)
    transport.close()
    inspected = subprocess.run(["docker", "inspect", transport.container_name], capture_output=True)
    assert inspected.returncode != 0


def test_exec_python_rejects_invalid_limits_before_spawn(monkeypatch):
    transport = DockerTransport("image")

    class Alive:
        def poll(self):
            return None

    monkeypatch.setattr(transport, "process", Alive())

    called = {"value": False}

    def fake_popen(*args, **kwargs):
        called["value"] = True
        raise AssertionError("must not spawn")

    monkeypatch.setattr("craft_workspace.transport.subprocess.Popen", fake_popen)

    with pytest.raises(WorkerError, match="invalid_max_output"):
        transport.exec_python("print(1)", max_output=40 * 1024 * 1024)
    with pytest.raises(WorkerError, match="invalid_timeout"):
        transport.exec_python("print(1)", timeout=0)
    with pytest.raises(WorkerError, match="invalid_input"):
        transport.exec_python("print(1)", input_bytes=b"x" * (10 * 1024 * 1024 + 1))

    assert called["value"] is False


def test_exec_python_write_timeout_cleans_owned_process(monkeypatch):
    transport = DockerTransport("image")

    class Alive:
        def poll(self):
            return None

    monkeypatch.setattr(transport, "process", Alive())

    class Pipe:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeProc:
        def __init__(self):
            self.stdin = Pipe()
            self.stdout = Pipe()
            self.stderr = Pipe()
            self.killed = False
            self.waited = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.waited = True
            return 1

    proc = FakeProc()

    monkeypatch.setattr("craft_workspace.transport.subprocess.Popen", lambda *a, **k: proc)
    monkeypatch.setattr(
        transport,
        "_write_with_deadline",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()),
    )

    with pytest.raises(WorkerError, match="exec_timeout"):
        transport.exec_python("print(1)", input_bytes=b"abc", timeout=0.5)

    assert proc.killed
    assert proc.waited
    assert proc.stdin.closed
    assert proc.stdout.closed
    assert proc.stderr.closed


def test_exec_large_input_partial_writes_and_real_pipe_timeout(monkeypatch):
    import subprocess
    import sys
    import time

    real_popen = subprocess.Popen
    transport = DockerTransport("image")
    processes = []

    class Alive:
        def poll(self):
            return None

    monkeypatch.setattr(transport, "process", Alive())

    def run_reader(*args, **kwargs):
        proc = real_popen(
            [sys.executable, "-c", "import sys; data=sys.stdin.buffer.read(); print(len(data))"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        processes.append(proc)
        return proc

    monkeypatch.setattr("craft_workspace.transport.subprocess.Popen", run_reader)
    data = b"x" * (2 * 1024 * 1024)
    result = transport.exec_python("trusted", input_bytes=data, timeout=3)
    assert result["stdout"] == str(len(data)).encode() + b"\n"
    assert processes[0].poll() == 0
    assert all(
        stream.closed
        for stream in (processes[0].stdin, processes[0].stdout, processes[0].stderr)
        if stream
    )

    def run_nonreader(*args, **kwargs):
        proc = real_popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        processes.append(proc)
        return proc

    monkeypatch.setattr("craft_workspace.transport.subprocess.Popen", run_nonreader)
    started = time.monotonic()
    with pytest.raises(WorkerError, match="exec_timeout"):
        transport.exec_python("trusted", input_bytes=data, timeout=0.1)
    assert time.monotonic() - started < 2
    assert processes[-1].poll() is not None


@pytest.mark.slow
@pytest.mark.docker
def test_managed_analysis_chart_is_returned_from_restricted_image(manager):
    session = manager.open()
    result = manager.execute(
        session,
        "import base64\n"
        "from pathlib import Path\n"
        "from craft_analysis.core import chart_spec, render_chart\n"
        "records = [{'day': 1, 'value': 2}, {'day': 2, 'value': 3}]\n"
        "spec = chart_spec(records, 'day', 'value')['spec']\n"
        "chart = render_chart(spec, 'png')\n"
        "Path('chart.png').write_bytes(base64.b64decode(chart['content']))",
    )
    assert result.error is None
    png = manager.read_file(session, "chart.png")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert any(artifact.name == "chart.png" for artifact in result.artifacts)
    manager.close(session)
