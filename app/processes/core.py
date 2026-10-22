"""Small lifecycle boundary over established process/container backends."""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from threading import RLock
from typing import Concatenate, Literal, Protocol

from app.processes.specs import LifecycleError, ProcessSpec, Spec, TCPReadiness, validate

State = Literal["stopped", "starting", "running", "stopping", "failed", "exited"]
Health = Literal["unchecked", "ready", "failed"]


def serialized[**P, R](
    method: Callable[Concatenate[ProcessManager, P], R],
) -> Callable[Concatenate[ProcessManager, P], R]:
    @wraps(method)
    def call(self: ProcessManager, *args: P.args, **kwargs: P.kwargs) -> R:
        with self.lock:
            return method(self, *args, **kwargs)

    return call


class Backend(Protocol):
    def register(self, spec: Spec) -> None: ...
    def start(self, spec: Spec) -> None: ...
    def stop(self, spec: Spec) -> None: ...
    def remove(self, spec: Spec) -> None: ...
    def state(self, spec: Spec) -> State: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class ServiceStatus:
    id: str
    state: State
    health: Health


def tcp_ready(probe: TCPReadiness) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", probe.port), timeout=probe.timeout):
            return True
    except OSError:
        return False


class ProcessManager:
    def __init__(
        self,
        processes: Backend,
        containers: Backend,
        *,
        probe: Callable[[TCPReadiness], bool] = tcp_ready,
    ):
        self.processes = processes
        self.containers = containers
        self.probe = probe
        self.services: dict[str, Spec] = {}
        self.health: dict[str, Health] = {}
        self.closed = False
        self.lock = RLock()

    def backend(self, spec: Spec) -> Backend:
        return self.processes if isinstance(spec, ProcessSpec) else self.containers

    def owned(self, service_id: str) -> Spec:
        if self.closed:
            raise LifecycleError("manager_closed")
        if service_id not in self.services:
            raise LifecycleError("unknown_service")
        return self.services[service_id]

    @serialized
    def add(self, spec: Spec) -> None:
        validate(spec)
        if self.closed:
            raise LifecycleError("manager_closed")
        if spec.id in self.services:
            raise LifecycleError("duplicate_service")
        if len(self.services) >= 16:
            raise LifecycleError("service_limit")
        self.backend(spec).register(spec)
        self.services[spec.id] = spec
        self.health[spec.id] = "unchecked"

    @serialized
    def status(self, service_id: str) -> ServiceStatus:
        spec = self.owned(service_id)
        state = self.backend(spec).state(spec)
        health = self.health[spec.id]
        if state == "running" and spec.readiness is not None:
            health = "ready" if self.probe(spec.readiness) else "failed"
        elif state != "running" and health == "ready":
            health = "unchecked"
        return ServiceStatus(spec.id, state, health)

    @serialized
    def start(self, service_id: str) -> ServiceStatus:
        spec = self.owned(service_id)
        self.health[spec.id] = "unchecked"
        if self.backend(spec).state(spec) not in {"starting", "running"}:
            self.backend(spec).start(spec)
        deadline = time.monotonic() + spec.ready_seconds
        while time.monotonic() < deadline:
            status = self.status(spec.id)
            if status.state == "running" and status.health != "failed":
                self.health[spec.id] = status.health
                return status
            if status.state in {"failed", "exited"}:
                self.health[spec.id] = "failed"
                raise LifecycleError("startup_failed")
            time.sleep(0.02)
        self.health[spec.id] = "failed"
        self.stop(spec.id)
        raise LifecycleError("readiness_timeout")

    @serialized
    def stop(self, service_id: str) -> None:
        spec = self.owned(service_id)
        self.backend(spec).stop(spec)
        deadline = time.monotonic() + spec.stop_seconds + 2
        while time.monotonic() < deadline:
            if self.backend(spec).state(spec) in {"stopped", "exited", "failed"}:
                return
            time.sleep(0.02)
        raise LifecycleError("shutdown_timeout")

    @serialized
    def remove(self, service_id: str) -> None:
        spec = self.owned(service_id)
        self.stop(service_id)
        self.backend(spec).remove(spec)
        del self.services[spec.id]
        del self.health[spec.id]

    @serialized
    def update(self, spec: Spec) -> None:
        validate(spec)
        old = self.owned(spec.id)
        running = self.backend(old).state(old) in {"starting", "running"}
        self.remove(spec.id)
        self.add(spec)
        if running:
            self.start(spec.id)

    @serialized
    def start_all(self) -> None:
        try:
            for spec in sorted(self.services.values(), key=lambda spec: (spec.priority, spec.id)):
                self.start(spec.id)
        except Exception:
            self.close()
            raise

    @serialized
    def close(self) -> None:
        if self.closed:
            return
        errors: list[Exception] = []
        for spec in sorted(
            self.services.values(), key=lambda spec: (spec.priority, spec.id), reverse=True
        ):
            try:
                self.remove(spec.id)
            except Exception as error:
                errors.append(error)
        for backend in (self.containers, self.processes):
            try:
                backend.close()
            except Exception as error:
                errors.append(error)
        if errors:
            raise LifecycleError("cleanup_incomplete") from None
        self.closed = True
