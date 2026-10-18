from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from craft_processes import ContainerSpec, LifecycleError, ProcessManager, ProcessSpec, TCPReadiness
from craft_processes.core import State
from craft_processes.specs import Spec


class FakeBackend:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.states: dict[str, State] = {}
        self.refuse = False

    def register(self, spec: Spec):
        if self.refuse:
            raise LifecycleError("backend_refused")
        self.states[spec.id] = "stopped"

    def start(self, spec: Spec):
        self.events.append(("start", spec.id))
        self.states[spec.id] = "running"

    def stop(self, spec: Spec):
        self.events.append(("stop", spec.id))
        self.states[spec.id] = "stopped"

    def remove(self, spec: Spec):
        del self.states[spec.id]

    def state(self, spec: Spec) -> State:
        return self.states[spec.id]

    def close(self):
        self.events.append(("close", "backend"))


def process(service_id="example", **kwargs):
    return ProcessSpec(id=service_id, command=("/usr/bin/true",), **kwargs)


def test_order_update_remove_and_closed():
    events = []
    backend = FakeBackend(events)
    manager = ProcessManager(backend, FakeBackend(events))
    manager.add(process("tools", priority=200, role="tool-worker"))
    manager.add(process("runtime", priority=50, role="agent-runtime"))
    manager.start_all()
    assert events[:2] == [("start", "runtime"), ("start", "tools")]
    assert manager.status("runtime").health == "unchecked"
    manager.update(process("runtime", priority=25, role="agent-runtime"))
    assert manager.services["runtime"].priority == 25
    manager.close()
    assert [event for event in events[-4:] if event[0] == "stop"] == [
        ("stop", "tools"),
        ("stop", "runtime"),
    ]
    manager.close()
    with pytest.raises(LifecycleError, match="manager_closed"):
        manager.add(process())


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"id": "../other"}, "invalid_service_id"),
        ({"command": ("sh", "-c", "echo unsafe")}, "absolute_executable_required"),
        ({"command": ("/bin/echo", "a\n[next]")}, "invalid_command"),
        ({"memory_bytes": 100}, "process_hard_limits_unsupported"),
        ({"nano_cpus": 100}, "process_hard_limits_unsupported"),
        ({"pids": 1}, "process_hard_limits_unsupported"),
        ({"priority": True}, "invalid_limits"),
        ({"restart": "unknown"}, "invalid_restart"),
        ({"readiness": TCPReadiness(port=1, timeout=float("nan"))}, "invalid_readiness"),
    ],
)
def test_refused_process_configuration(changes, code):
    backend = FakeBackend()
    manager = ProcessManager(backend, FakeBackend())
    with pytest.raises(LifecycleError, match=code):
        manager.add(replace(process(), **changes))
    assert manager.services == {} and backend.states == {}


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"image": "python:latest"}, "pinned_image_required"),
        ({"user": "0:0"}, "non_root_user_required"),
        ({"memory_bytes": 1}, "invalid_container_limits"),
        ({"nano_cpus": 0}, "invalid_container_limits"),
        ({"pids": 100000}, "invalid_container_limits"),
    ],
)
def test_refused_container_configuration(changes, code):
    manager = ProcessManager(FakeBackend(), FakeBackend())
    with pytest.raises(LifecycleError, match=code):
        manager.add(replace(ContainerSpec(id="container", image="sha256:" + "a" * 64), **changes))
    assert manager.services == {}


def test_duplicates_backend_refusal_and_readiness_loss():
    backend = FakeBackend()
    ready = [True]
    manager = ProcessManager(backend, FakeBackend(), probe=lambda _: ready[0])
    manager.add(process(readiness=TCPReadiness(port=12345)))
    with pytest.raises(LifecycleError, match="duplicate_service"):
        manager.add(process())
    assert manager.start("example").health == "ready"
    ready[0] = False
    assert manager.status("example").health == "failed"
    backend.refuse = True
    with pytest.raises(LifecycleError, match="backend_refused"):
        manager.add(process("refused"))
    assert "refused" not in manager.services
    with pytest.raises(LifecycleError, match="unknown_service"):
        manager.remove("foreign")
    manager.close()


def test_readiness_timeout_stops_and_is_visible():
    backend = FakeBackend()
    manager = ProcessManager(backend, FakeBackend(), probe=lambda _: False)
    manager.add(process(readiness=TCPReadiness(port=12345), ready_seconds=1))
    with pytest.raises(LifecycleError, match="readiness_timeout"):
        manager.start("example")
    assert manager.status("example").health == "failed"
    assert backend.states["example"] == "stopped"
    manager.close()


def test_failed_update_validation_preserves_old_service():
    backend = FakeBackend()
    manager = ProcessManager(backend, FakeBackend())
    manager.add(process())
    manager.start("example")
    with pytest.raises(LifecycleError, match="process_hard_limits_unsupported"):
        manager.update(process(memory_bytes=100))
    assert backend.states["example"] == "running"
    manager.close()


def test_concurrent_add_and_start_are_serialized():
    backend = FakeBackend()
    manager = ProcessManager(backend, FakeBackend())

    def add():
        try:
            manager.add(process())
            return "added"
        except LifecycleError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: add(), range(2))) == ["added", "duplicate_service"]
        states = list(pool.map(lambda _: manager.start("example").state, range(2)))
    assert states == ["running", "running"]
    assert backend.events.count(("start", "example")) == 1
    manager.close()
