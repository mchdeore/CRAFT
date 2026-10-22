from collections.abc import Iterator
from pathlib import Path

import fakeredis
import pytest

from app.infrastructure.config.settings import Settings
from app.library import queue
from app.tests.support.library import library_context


@pytest.fixture
def fake_queue(monkeypatch):
    server = fakeredis.FakeServer()

    def open_connection(settings):
        return fakeredis.FakeRedis(server=server)

    monkeypatch.setattr(queue, "open_connection", open_connection)
    return server


@pytest.fixture
def library(settings: Settings, tmp_path: Path) -> Iterator[dict]:
    with library_context(settings, tmp_path) as state:
        yield state
