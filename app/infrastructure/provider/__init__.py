"""Original-file provider and stable version access."""

from app.infrastructure.provider.contracts import Provider as Provider
from app.infrastructure.provider.factory import get_provider as get_provider
from app.infrastructure.provider.simulated import SimulatedProvider as SimulatedProvider
