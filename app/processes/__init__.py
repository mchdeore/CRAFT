from pathlib import Path

from app.processes.core import ProcessManager, ServiceStatus
from app.processes.docker import DockerBackend
from app.processes.specs import ContainerSpec, LifecycleError, ProcessSpec, TCPReadiness
from app.processes.supervisord import SupervisorBackend


def open_manager(root: Path) -> ProcessManager:
    processes = SupervisorBackend(root)
    return ProcessManager(processes, DockerBackend(processes.owner))


__all__ = [
    "ContainerSpec",
    "LifecycleError",
    "ProcessManager",
    "ProcessSpec",
    "ServiceStatus",
    "TCPReadiness",
    "open_manager",
]
