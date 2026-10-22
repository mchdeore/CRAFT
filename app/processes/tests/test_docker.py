from types import SimpleNamespace

import pytest
from docker.errors import DockerException, NotFound

from app.processes import ContainerSpec, LifecycleError, ProcessManager
from app.processes.docker import DockerBackend
from app.processes.tests.test_manager import FakeBackend


class FakeContainer:
    def __init__(self, kwargs):
        self.id = "generated-id"
        self.starts = 0
        self.removed = False
        self.attrs = {
            "Config": {"Labels": kwargs["labels"], "User": kwargs["user"]},
            "HostConfig": {
                "Memory": kwargs["mem_limit"],
                "NanoCpus": kwargs["nano_cpus"],
                "PidsLimit": kwargs["pids_limit"],
                "NetworkMode": kwargs["network_mode"],
                "ReadonlyRootfs": kwargs["read_only"],
                "Privileged": False,
                "CapDrop": kwargs["cap_drop"],
                "SecurityOpt": kwargs["security_opt"],
            },
            "State": {"Running": False, "Restarting": False, "ExitCode": 0},
        }

    def reload(self):
        pass

    def start(self):
        self.starts += 1
        self.attrs["State"]["Running"] = True

    def stop(self, timeout):
        self.attrs["State"]["Running"] = False

    def remove(self):
        self.removed = True


class FakeContainers:
    def __init__(self):
        self.created = None
        self.kwargs = {}
        self.wrong_limits = False
        self.refuse = False
        self.unknown_acceptance = False
        self.create_count = 0

    def create(self, image, command, **kwargs):
        if self.refuse:
            raise DockerException("generated backend diagnostic")
        self.kwargs = kwargs
        self.created = FakeContainer(kwargs)
        self.create_count += 1
        if self.unknown_acceptance:
            self.unknown_acceptance = False
            raise DockerException("generated lost create response")
        if self.wrong_limits:
            self.created.attrs["HostConfig"]["Memory"] = 0
        return self.created

    def get(self, container_id):
        if self.created is None or self.created.removed:
            raise NotFound("generated absence")
        assert self.created.id == container_id or container_id.startswith("craft-generated-owner-")
        return self.created

    def list(self, all, filters):
        return [self.created] if self.created is not None and not self.created.removed else []


def setup_backend():
    containers = FakeContainers()
    client = SimpleNamespace(containers=containers, close=lambda: None)
    backend = DockerBackend("generated-owner", client)
    return ProcessManager(FakeBackend(), backend), backend, containers


def test_container_inspected_before_execution_and_owned_cleanup():
    manager, backend, containers = setup_backend()
    spec = ContainerSpec(id="worker", image="sha256:" + "a" * 64)
    manager.add(spec)
    assert manager.start(spec.id).state == "running"
    assert containers.created is not None
    assert containers.created.starts == 1
    assert containers.kwargs["log_config"]["Type"] == "none"
    assert "volumes" not in containers.kwargs and "environment" not in containers.kwargs
    assert containers.kwargs["restart_policy"] == {"Name": "on-failure", "MaximumRetryCount": 2}
    containers.created.attrs["Config"]["Labels"]["craft.process-owner"] = "foreign"
    with pytest.raises(LifecycleError, match="container_ownership_mismatch"):
        manager.remove(spec.id)
    assert not containers.created.removed
    containers.created.attrs["Config"]["Labels"]["craft.process-owner"] = backend.owner
    manager.close()
    assert containers.created.removed and not backend.ids


def test_missing_limits_refuse_before_start_and_remain_cleanup_owned():
    manager, backend, containers = setup_backend()
    containers.wrong_limits = True
    spec = ContainerSpec(id="limits", image="sha256:" + "a" * 64)
    manager.add(spec)
    with pytest.raises(LifecycleError, match="container_limits_not_enforced"):
        manager.start(spec.id)
    assert containers.created is not None
    assert containers.created.starts == 0
    assert manager.status(spec.id).state == "stopped"
    manager.close()
    assert containers.created.removed and not backend.ids


def test_backend_error_sanitized_and_not_healthy():
    manager, _, containers = setup_backend()
    containers.refuse = True
    spec = ContainerSpec(id="refused", image="sha256:" + "a" * 64)
    manager.add(spec)
    with pytest.raises(LifecycleError, match="^container_backend_failed$"):
        manager.start(spec.id)
    assert manager.status(spec.id).state == "stopped"
    assert manager.status(spec.id).health == "unchecked"
    manager.close()


def test_lost_create_response_is_reconciled_without_duplicate_execution():
    manager, _, containers = setup_backend()
    containers.unknown_acceptance = True
    spec = ContainerSpec(id="uncertain", image="sha256:" + "a" * 64)
    manager.add(spec)
    with pytest.raises(LifecycleError, match="container_backend_failed"):
        manager.start(spec.id)
    assert containers.created is not None and containers.created.starts == 0
    assert manager.start(spec.id).state == "running"
    assert containers.create_count == 1 and containers.created.starts == 1
    manager.close()
    assert containers.created.removed
