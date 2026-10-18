from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import msgspec

from app.infrastructure.config.settings import Settings
from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.mcp.stdio import local_caller
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library import storage
from app.library.index import build
from app.seed.base import seed


@contextmanager
def library_context(settings: Settings, tmp_path: Path) -> Iterator[dict]:
    configured = msgspec.structs.replace(settings, provider_root=str(tmp_path))
    sessions, engine = make_owner_sessions(configured)
    callers = {}
    try:
        seed(configured)
        build(configured)
        for name in ["demo", "restricted", "admin"]:
            callers[name] = replace(local_caller(name, configured), origin="python")
        yield {"settings": configured, "callers": callers, "root": tmp_path, "sessions": sessions}
    finally:
        try:
            provider = SimulatedProvider(tmp_path)
            item_ids = list(provider.items())
            with sessions.begin() as session:
                storage.clear_index(session, item_ids)
                session.execute(
                    m.provider_items.delete().where(m.provider_items.c.item.in_(item_ids))
                )
                session.execute(
                    m.index_cursor.delete().where(m.index_cursor.c.provider == str(provider.root))
                )
        finally:
            for caller in callers.values():
                assert caller.sessions is not None
                caller.sessions.kw["bind"].dispose()
            engine.dispose()
