"""/health/live and /health/ready: anonymous, outside /api, reveal nothing.
Plain routes in S0; S1 makes them @operation(PUBLIC, get=…)."""

import asyncio

import psycopg
from alembic.script import ScriptDirectory
from starlette.requests import Request
from starlette.responses import Response

from app.infrastructure.config.settings import Settings
from app.infrastructure.database.engine import conninfo
from app.infrastructure.paths import MIGRATIONS
from app.infrastructure.shapes import Health, json_response

READY_DEADLINE_S = 3


def migration_head() -> str | None:
    """The newest migration in migrations/versions (None until S1 adds the first)."""
    return ScriptDirectory(str(MIGRATIONS)).get_current_head()


def live() -> Response:
    """Up while the process is up; never touches the database."""
    return json_response(Health(status="ok"))


async def ready(request: Request) -> Response:
    """200 when the database answers at the expected migration within 3 s, else 503.
    Its own connection under a hard deadline, so a hung database can't hang the probe."""
    try:
        async with asyncio.timeout(READY_DEADLINE_S):
            current = await current_migration(request.app.state.settings)
        ok = current == request.app.state.migration_head
    except (TimeoutError, psycopg.Error):
        ok = False
    return json_response(Health(status="ok" if ok else "unavailable"), 200 if ok else 503)


async def current_migration(settings: Settings) -> str | None:
    async with await psycopg.AsyncConnection.connect(conninfo(settings)) as connection:
        cursor = await connection.execute("select to_regclass('alembic_version') is not null")
        row = await cursor.fetchone()
        if not (row and row[0]):
            return None
        cursor = await connection.execute("select version_num from alembic_version")
        row = await cursor.fetchone()
        return row[0] if row else None
