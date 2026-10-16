"""/health/live and /health/ready: anonymous, outside /api, reveal nothing.
Plain routes in S0; S1 makes them @operation(PUBLIC, get=…)."""

import asyncio

import psycopg
from alembic.script import ScriptDirectory

from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.database.engine import conninfo
from app.infrastructure.operations import operation
from app.infrastructure.paths import MIGRATIONS
from app.infrastructure.shapes import Health

READY_DEADLINE_S = 3


def migration_head() -> str | None:
    """The newest migration in migrations/versions (None until S1 adds the first)."""
    return ScriptDirectory(str(MIGRATIONS)).get_current_head()


@operation(PUBLIC, get="/health/live")
def live(caller: Caller) -> Health:
    """Up while the process is up; never touches the database."""
    return Health(status="ok")


@operation(PUBLIC, get="/health/ready")
async def ready(caller: Caller) -> Health:
    """200 when the database answers at the expected migration within 3 s, else 503.
    Its own connection under a hard deadline, so a hung database can't hang the probe."""
    try:
        async with asyncio.timeout(READY_DEADLINE_S):
            assert caller.settings is not None
            current = await current_migration(caller.settings)
        ok = current == caller.migration_head
    except (TimeoutError, psycopg.Error):
        ok = False
    return Health(status="ok" if ok else "unavailable")


async def current_migration(settings: Settings) -> str | None:
    async with await psycopg.AsyncConnection.connect(conninfo(settings)) as connection:
        cursor = await connection.execute("select to_regclass('alembic_version') is not null")
        row = await cursor.fetchone()
        if not (row and row[0]):
            return None
        cursor = await connection.execute("select version_num from alembic_version")
        row = await cursor.fetchone()
        return row[0] if row else None
