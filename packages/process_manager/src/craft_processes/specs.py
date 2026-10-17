"""Controller-owned specifications; these are never model or browser inputs."""

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Restart = Literal["never", "on-failure", "always"]
Role = Literal["agent-runtime", "tool-worker", "service"]


class LifecycleError(RuntimeError):
    """Only fixed error codes cross this boundary."""


@dataclass(frozen=True, kw_only=True)
class TCPReadiness:
    port: int
    timeout: float = 0.2


@dataclass(frozen=True, kw_only=True)
class ServiceSpec:
    id: str
    role: Role = "service"
    priority: int = 100
    restart: Restart = "on-failure"
    retries: int = 2
    stop_seconds: int = 3
    ready_seconds: int = 5
    readiness: TCPReadiness | None = None


@dataclass(frozen=True, kw_only=True)
class ProcessSpec(ServiceSpec):
    command: tuple[str, ...]
    memory_bytes: int | None = None
    nano_cpus: int | None = None
    pids: int | None = None


@dataclass(frozen=True, kw_only=True)
class ContainerSpec(ServiceSpec):
    image: str
    command: tuple[str, ...] = ()
    user: str = "10001:10001"
    memory_bytes: int = 128 * 1024 * 1024
    nano_cpus: int = 500_000_000
    pids: int = 64


Spec = ProcessSpec | ContainerSpec


def integer(value: int, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def validate(spec: Spec) -> None:
    if not isinstance(spec, (ProcessSpec, ContainerSpec)):
        raise LifecycleError("invalid_spec")
    if not isinstance(spec.id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", spec.id):
        raise LifecycleError("invalid_service_id")
    if spec.role not in {"agent-runtime", "tool-worker", "service"}:
        raise LifecycleError("invalid_role")
    if spec.restart not in {"never", "on-failure", "always"}:
        raise LifecycleError("invalid_restart")
    bounds = [
        (spec.priority, 0, 999),
        (spec.retries, 0, 5),
        (spec.stop_seconds, 1, 15),
        (spec.ready_seconds, 1, 30),
    ]
    if any(not integer(value, low, high) for value, low, high in bounds):
        raise LifecycleError("invalid_limits")
    if not isinstance(spec.command, tuple) or any(
        not isinstance(arg, str) or not arg or any(c in arg for c in "\x00\n\r")
        for arg in spec.command
    ):
        raise LifecycleError("invalid_command")
    validate_readiness(spec.readiness)
    if isinstance(spec, ProcessSpec):
        validate_process(spec)
    else:
        validate_container(spec)


def validate_readiness(probe: TCPReadiness | None) -> None:
    if probe is None:
        return
    if not isinstance(probe, TCPReadiness) or not integer(probe.port, 1, 65535):
        raise LifecycleError("invalid_readiness")
    if (
        type(probe.timeout) not in {float, int}
        or not math.isfinite(probe.timeout)
        or not 0 < probe.timeout <= 1
    ):
        raise LifecycleError("invalid_readiness")


def validate_process(spec: ProcessSpec) -> None:
    if not spec.command or not Path(spec.command[0]).is_absolute():
        raise LifecycleError("absolute_executable_required")
    if any(limit is not None for limit in (spec.memory_bytes, spec.nano_cpus, spec.pids)):
        raise LifecycleError("process_hard_limits_unsupported")


def validate_container(spec: ContainerSpec) -> None:
    if not isinstance(spec.image, str) or not re.fullmatch(
        r"(?:[a-zA-Z0-9./:_-]+@)?sha256:[a-f0-9]{64}", spec.image
    ):
        raise LifecycleError("pinned_image_required")
    if not isinstance(spec.user, str) or not re.fullmatch(
        r"[1-9][0-9]{0,5}:[1-9][0-9]{0,5}", spec.user
    ):
        raise LifecycleError("non_root_user_required")
    bounds = [
        (spec.memory_bytes, 8 * 1024 * 1024, 2 * 1024**3),
        (spec.nano_cpus, 100_000_000, 8_000_000_000),
        (spec.pids, 16, 4096),
    ]
    if any(not integer(value, low, high) for value, low, high in bounds):
        raise LifecycleError("invalid_container_limits")
