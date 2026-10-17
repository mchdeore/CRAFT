"""Select the configured source once at every application source boundary."""

from pathlib import Path

from app.infrastructure.provider.contracts import Provider
from app.infrastructure.provider.simulated import SimulatedProvider


def get_provider(root: str | Path, *, clock: str | None = None) -> Provider:
    return SimulatedProvider(root, clock=clock)
