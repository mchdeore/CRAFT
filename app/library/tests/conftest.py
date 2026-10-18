from collections.abc import Iterator
from pathlib import Path

import pytest

from app.infrastructure.config.settings import Settings
from app.tests.support.library import library_context


@pytest.fixture
def library(settings: Settings, tmp_path: Path) -> Iterator[dict]:
    with library_context(settings, tmp_path) as state:
        yield state
