"""Docker-owned labels and enforced limits; no volumes, network or secrets."""

from contextlib import contextmanager
from typing import Any

from docker.errors import DockerException, NotFound
from docker.types import LogConfig

import docker
from craft_processes.core import State
from craft_processes.specs import ContainerSpec, LifecycleError, Spec, validate


@contextmanager
def safe_docker():
    try:
        yield
    except DockerException:
        raise LifecycleError("container_backend_failed") from None


class DockerBackend:
    def __init__(self, owner: str, client: Any = None):
        self.owner = owner
        self.client = client
        self.specs: dict[str, ContainerSpec] = {}
        self.ids: dict[str, str] = {}
        self.attempted: set[str] = set()

    def api(self) -> Any:
        if self.client is None:
            with safe_docker():
                self.client = docker.from_env(timeout=20)
        return self.client

    def owned(self, spec: Spec) -> ContainerSpec:
        if not isinstance(spec, ContainerSpec) or self.specs.get(spec.id) != spec:
            raise LifecycleError("container_ownership_mismatch")
        return spec

    def container(self, spec: Spec):
        self.owned(spec)
        with safe_docker():
            container = self.api().containers.get(self.ids[spec.id])
            container.reload()
        labels = container.attrs["Config"].get("Labels", {})
        if (
            labels.get("craft.process-owner") != self.owner
            or labels.get("craft.service-id") != spec.id
        ):
            raise LifecycleError("container_ownership_mismatch")
        return container

    def discover(self, spec: Spec) -> None:
        self.owned(spec)
        if spec.id in self.ids or spec.id not in self.attempted:
            return
        with safe_docker():
            try:
                container = self.api().containers.get(f"craft-{self.owner}-{spec.id}")
            except NotFound:
                return
            container.reload()
        labels = container.attrs["Config"].get("Labels", {})
        if (
            labels.get("craft.process-owner") != self.owner
            or labels.get("craft.service-id") != spec.id
        ):
            raise LifecycleError("container_ownership_mismatch")
        self.ids[spec.id] = container.id

    def register(self, spec: Spec) -> None:
        validate(spec)
        if not isinstance(spec, ContainerSpec) or spec.id in self.specs:
            raise LifecycleError("container_ownership_mismatch")
        self.specs[spec.id] = spec

    def start(self, spec: Spec) -> None:
        spec = self.owned(spec)
        self.discover(spec)
        if spec.id in self.ids:
            self.inspect_limits(spec)
            with safe_docker():
                self.container(spec).start()
            return
        restart = {"never": "no", "on-failure": "on-failure", "always": "always"}[spec.restart]
        policy: dict[str, Any] = {"Name": restart}
        if restart == "on-failure":
            policy["MaximumRetryCount"] = spec.retries
        uid, gid = spec.user.split(":")
        self.attempted.add(spec.id)
        with safe_docker():
            container = self.api().containers.create(
                spec.image,
                list(spec.command) or None,
                name=f"craft-{self.owner}-{spec.id}",
                user=spec.user,
                network_mode="none",
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit=spec.memory_bytes,
                nano_cpus=spec.nano_cpus,
                pids_limit=spec.pids,
                tmpfs={"/tmp": f"rw,noexec,nosuid,size=16m,uid={uid},gid={gid}"},
                log_config=LogConfig(type="none"),
                init=True,
                use_config_proxy=False,
                restart_policy=policy,
                labels={"craft.process-owner": self.owner, "craft.service-id": spec.id},
            )
        self.ids[spec.id] = container.id
        self.inspect_limits(spec)
        with safe_docker():
            container.start()

    def inspect_limits(self, spec: ContainerSpec) -> None:
        attrs = self.container(spec).attrs
        host, config = attrs["HostConfig"], attrs["Config"]
        expected = {
            "Memory": spec.memory_bytes,
            "NanoCpus": spec.nano_cpus,
            "PidsLimit": spec.pids,
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
        }
        if (
            any(host.get(key) != value for key, value in expected.items())
            or config.get("User") != spec.user
            or host.get("CapDrop") != ["ALL"]
            or "no-new-privileges:true" not in host.get("SecurityOpt", [])
        ):
            raise LifecycleError("container_limits_not_enforced")

    def stop(self, spec: Spec) -> None:
        self.owned(spec)
        self.discover(spec)
        if spec.id in self.ids:
            with safe_docker():
                self.container(spec).stop(timeout=spec.stop_seconds)

    def state(self, spec: Spec) -> State:
        self.owned(spec)
        self.discover(spec)
        if spec.id not in self.ids:
            return "stopped"
        attrs = self.container(spec).attrs["State"]
        if attrs.get("Restarting"):
            return "starting"
        if attrs.get("Running"):
            return "running"
        return "failed" if attrs.get("ExitCode", 0) else "stopped"

    def remove(self, spec: Spec) -> None:
        self.owned(spec)
        self.discover(spec)
        if spec.id in self.ids:
            with safe_docker():
                self.container(spec).remove()
            del self.ids[spec.id]
        del self.specs[spec.id]
        self.attempted.discard(spec.id)

    def close(self) -> None:
        if self.ids:
            raise LifecycleError("container_cleanup_incomplete")
        if self.client is not None:
            with safe_docker():
                remaining = self.client.containers.list(
                    all=True, filters={"label": f"craft.process-owner={self.owner}"}
                )
            if remaining:
                raise LifecycleError("container_cleanup_incomplete")
            self.client.close()
