import os
import signal
import socket
import stat
import sys
import time
from dataclasses import replace

import pytest

from craft_processes import LifecycleError, ProcessManager, ProcessSpec, TCPReadiness
from craft_processes.supervisord import SupervisorBackend
from packages.process_manager.tests.test_manager import FakeBackend


def wait_until(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.03)
    pytest.fail("owned process condition timed out")


@pytest.fixture
def managed(tmp_path):
    backend = SupervisorBackend(tmp_path)
    manager = ProcessManager(backend, FakeBackend())
    try:
        yield manager, backend
    finally:
        manager.close()
        assert backend.daemon.poll() == 0
        assert not backend.root.exists() and not backend.ipc.exists()


def test_real_start_restart_intentional_stop_and_permissions(managed):
    manager, backend = managed
    spec = ProcessSpec(
        id="runtime",
        role="agent-runtime",
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
    )
    manager.add(spec)
    assert stat.S_IMODE(backend.socket.stat().st_mode) == 0o600
    assert stat.S_IMODE(backend.root.stat().st_mode) == 0o700
    assert manager.start(spec.id).state == "running"
    pid = backend.rpc.supervisor.getProcessInfo("craft-runtime")["pid"]
    os.kill(pid, signal.SIGKILL)
    wait_until(
        lambda: backend.rpc.supervisor.getProcessInfo("craft-runtime")["pid"] not in {0, pid}
    )
    wait_until(lambda: manager.status(spec.id).state == "running")
    manager.stop(spec.id)
    time.sleep(1.2)
    assert manager.status(spec.id).state == "stopped"
    assert backend.rpc.supervisor.getProcessInfo("craft-runtime")["pid"] == 0
    manager.remove(spec.id)
    assert spec.id not in backend.specs


@pytest.mark.parametrize(
    "restart,code,expected_restart",
    [("never", 7, False), ("on-failure", 0, False), ("on-failure", 7, True), ("always", 0, True)],
)
def test_real_exit_policies(managed, tmp_path, restart, code, expected_restart):
    manager, backend = managed
    count = tmp_path / "starts"
    script = (
        f"from pathlib import Path; import time,sys; p=Path({str(count)!r}); "
        "p.write_text(p.read_text()+'x' if p.exists() else 'x'); "
        f"time.sleep(1.4); sys.exit({code})"
    )
    spec = ProcessSpec(id="policy", command=(sys.executable, "-c", script), restart=restart)
    manager.add(spec)
    manager.start(spec.id)
    if expected_restart:
        wait_until(lambda: len(count.read_text()) >= 2)
    else:
        wait_until(lambda: manager.status(spec.id).state == "exited")
        time.sleep(1.2)
        assert count.read_text() == "x"


def test_real_readiness_and_group_kill(managed, tmp_path):
    manager, backend = managed
    with socket.socket() as socket_probe:
        socket_probe.bind(("127.0.0.1", 0))
        port = socket_probe.getsockname()[1]
    child_pid = tmp_path / "child"
    script = (
        "import socket,signal,subprocess,sys,time; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "child=subprocess.Popen([sys.executable,'-c','import signal,time; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)']); "
        f"open({str(child_pid)!r},'w').write(str(child.pid)); "
        f"s=socket.socket(); s.bind(('127.0.0.1',{port})); s.listen(); time.sleep(60)"
    )
    spec = ProcessSpec(
        id="tools",
        role="tool-worker",
        command=(sys.executable, "-c", script),
        readiness=TCPReadiness(port=port),
        stop_seconds=1,
    )
    manager.add(spec)
    assert manager.start(spec.id).health == "ready"
    descendant = int(child_pid.read_text())
    started = time.monotonic()
    manager.stop(spec.id)
    assert time.monotonic() - started < 4
    wait_until(lambda: not alive(descendant))
    assert manager.status(spec.id).state == "stopped"
    with pytest.raises(LifecycleError, match="process_ownership_mismatch"):
        backend.stop(replace(spec, command=("/usr/bin/true",)))


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_real_argv_no_shell_or_provider_environment(managed, tmp_path, monkeypatch):
    manager, backend = managed
    output = tmp_path / "observed"
    marker = tmp_path / "shell-was-run"
    monkeypatch.setenv("CRAFT_MODEL_KEY", "generated-placeholder")
    script = (
        f"import os,sys,time; open({str(output)!r},'w').write("
        "sys.argv[1]+'|'+str('CRAFT_MODEL_KEY' in os.environ)); time.sleep(20)"
    )
    argument = f"$(touch {marker}) ; % ' quoted"
    spec = ProcessSpec(id="argv", command=(sys.executable, "-c", script, argument))
    manager.add(spec)
    manager.start(spec.id)
    assert output.read_text() == argument + "|False"
    assert not marker.exists()
    assert "stdout_logfile=/dev/null" in backend.program_config(spec)


def test_constructor_failure_reaps_only_owned_daemon(tmp_path, monkeypatch):
    created = []
    from craft_processes.supervisord import subprocess

    popen = subprocess.Popen

    def spawn(*args, **kwargs):
        daemon = popen(*args, **kwargs)
        created.append(daemon)
        return daemon

    def fail(_):
        raise OSError("generated connection failure")

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(SupervisorBackend, "connect", fail)
    with pytest.raises(LifecycleError, match="process_backend_unavailable"):
        SupervisorBackend(tmp_path)
    assert len(created) == 1 and created[0].poll() is not None
    assert list(tmp_path.iterdir()) == []
