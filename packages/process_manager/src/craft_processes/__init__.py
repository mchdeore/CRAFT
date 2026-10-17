from pathlib import Path

from craft_processes.core import ProcessManager, ServiceStatus
from craft_processes.docker import DockerBackend
from craft_processes.specs import ContainerSpec, LifecycleError, ProcessSpec, TCPReadiness
from craft_processes.supervisord import SupervisorBackend


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
