from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import msgspec
import pytest
from sqlalchemy import select

from app.infrastructure.config.settings import Settings
from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.mcp.stdio import local_caller
from app.library.index import build
from app.seed.base import seed


@pytest.fixture
def library(settings: Settings, tmp_path: Path) -> Iterator[dict]:
    configured = msgspec.structs.replace(settings, provider_root=str(tmp_path))
    seed(configured)
    build(configured)
    sessions, engine = make_owner_sessions(configured)
    callers = {
        name: replace(local_caller(name, configured), origin="python")
        for name in ["demo", "restricted", "admin"]
    }
    yield {"settings": configured, "callers": callers, "root": tmp_path, "sessions": sessions}
    with sessions.begin() as session:
        doc_ids = list(session.execute(select(m.doc_versions.c.id)).scalars())
        chunk_ids = list(session.execute(select(m.chunks.c.id)).scalars())
        session.execute(m.vectors.delete().where(m.vectors.c.owner_id.in_([*doc_ids, *chunk_ids])))
        session.execute(m.version_diffs.delete())
        session.execute(m.doc_versions.delete())
        session.execute(m.provider_items.delete())
        session.execute(m.index_cursor.delete())
    for caller in callers.values():
        assert caller.sessions is not None
        caller.sessions.kw["bind"].dispose()
    engine.dispose()
