from collections.abc import Iterator

import pytest

from app.infrastructure.operations.context import current_call
from app.infrastructure.operations.registry import registry


@pytest.fixture(autouse=True)
def restore_registry(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    # Inline fixture operations use a synthetic identity under every import mode.
    monkeypatch.setattr(request.module, "__name__", "operation_probe")
    previous = registry.copy()
    yield
    registry.clear()
    registry.update(previous)
    current_call.set(None)
