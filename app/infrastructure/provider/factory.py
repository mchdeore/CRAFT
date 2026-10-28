"""Select the configured source once at every application source boundary."""

from pathlib import Path

from app.infrastructure.provider.contracts import Provider
from app.infrastructure.provider.simulated import SimulatedProvider


def get_provider(root: str | Path, *, clock: str | None = None) -> SimulatedProvider:
    """The simulated source, the only one CRAFT writes to (seed and ``craft provider``)."""
    return SimulatedProvider(root, clock=clock)


def source_provider(settings) -> Provider:
    """The source ``CRAFT_PROVIDER`` names: simulated reads originals from the folder,
    livelink_test through a LiveLink-shaped API. Switching it changes nothing else."""
    if settings.provider == "livelink_test":
        from app.infrastructure.provider.livelink import LiveLinkProvider

        return LiveLinkProvider(
            settings.provider_root, settings.provider_url, settings.provider_token
        )
    return SimulatedProvider(settings.provider_root)
