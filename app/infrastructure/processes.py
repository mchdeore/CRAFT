"""Controller-only attachment; no public operation creates processes."""

from pathlib import Path

from app.infrastructure.config.settings import Settings
from craft_processes import ProcessManager, open_manager


def open_processes(settings: Settings) -> ProcessManager | None:
    return open_manager(Path(settings.process_root)) if settings.process_root else None
