"""Resolve one local account and serve MCP until standard input closes."""

import asyncio
import sys

from mcp.server.stdio import stdio_server
from sqlalchemy import select

from app.infrastructure.access import Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.database.metadata import metadata
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.logging import configure_logging, instrument_sql
from app.infrastructure.mcp.server import build_server
from app.infrastructure.operations.registry import registry


def local_caller(username: str, settings: Settings) -> Caller:
    engine = make_engine(settings)
    instrument_sql(engine)
    sessions = make_sessions(engine)
    users = metadata.tables["users"]
    roles = metadata.tables["user_roles"]
    with unit_of_work(sessions, read_only=True) as session:
        user = (
            session.execute(
                select(users).where(
                    users.c.username == username.lower(), users.c.disabled_at.is_(None)
                )
            )
            .mappings()
            .first()
        )
        if user is None:
            engine.dispose()
            raise SystemExit(2)
        assigned = frozenset(
            session.execute(select(roles.c.role).where(roles.c.user_id == user["id"])).scalars()
        )
    return Caller(
        user_id=user["id"], roles=assigned, sessions=sessions, settings=settings, origin="mcp"
    )


async def serve(caller: Caller) -> None:
    server = build_server(registry, caller)
    async with stdio_server() as (incoming, outgoing):
        await server.run(incoming, outgoing, server.create_initialization_options())


def run_stdio(username: str) -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level, sys.stderr)
    try:
        caller = local_caller(username, settings)
    except SystemExit:
        print("unknown or disabled account", file=sys.stderr)
        raise
    try:
        asyncio.run(serve(caller))
    finally:
        if caller.sessions is not None:
            caller.sessions.kw["bind"].dispose()
