from collections.abc import Iterator

import pytest

from app.infrastructure.operations.context import current_call
from app.infrastructure.operations.registry import registry


@pytest.fixture(autouse=True)
def restore_registry() -> Iterator[None]:
    previous = registry.copy()
    yield
    registry.clear()
    registry.update(previous)
    current_call.set(None)
