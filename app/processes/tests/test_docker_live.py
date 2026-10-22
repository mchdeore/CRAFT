import time
from uuid import uuid4

import docker
import pytest
from docker.errors import NotFound

from app.processes import ContainerSpec, ProcessManager
from app.processes.docker import DockerBackend
from app.processes.tests.test_manager import FakeBackend


@pytest.mark.docker
def test_real_container_limits_and_owned_removal():
    client = docker.from_env(timeout=20)
    owner = uuid4().hex
    backend = DockerBackend(owner, client)
    manager = ProcessManager(FakeBackend(), backend)
    ids = []
    try:
        image = client.images.get("craft-user-workspace:local-pilot").id
        assert image is not None
        spec = ContainerSpec(
            id="tool-worker",
            role="tool-worker",
            image=image,
            command=("python", "-c", "import time; time.sleep(60)"),
            stop_seconds=1,
        )
        manager.add(spec)
        assert manager.start(spec.id).state == "running"
        container = backend.container(spec)
        ids.append(container.id)
        backend.inspect_limits(spec)
        host = container.attrs["HostConfig"]
        assert host["LogConfig"]["Type"] == "none"
        assert host.get("Binds") in (None, [], ())
        assert not any(
            value.startswith(("HTTP_PROXY=", "HTTPS_PROXY="))
            for value in container.attrs["Config"]["Env"]
        )
        manager.stop(spec.id)
        time.sleep(0.2)
        assert manager.status(spec.id).state != "running"
        manager.remove(spec.id)
    finally:
        manager.close()
    verification = docker.from_env(timeout=20)
    try:
        assert (
            verification.containers.list(
                all=True, filters={"label": f"craft.process-owner={owner}"}
            )
            == []
        )
        for container_id in ids:
            with pytest.raises(NotFound):
                verification.containers.get(container_id)
    finally:
        verification.close()
