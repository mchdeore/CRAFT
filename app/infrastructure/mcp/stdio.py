"""Resolve one local account and serve MCP until standard input closes."""

import asyncio
import sys

from mcp.server.stdio import stdio_server

from app.infrastructure.access import Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.logging import configure_logging, instrument_sql
from app.infrastructure.mcp.server import build_server
from app.infrastructure.operations.registry import registry


def local_caller(username: str, settings: Settings) -> Caller:
    """The enabled account `username` with its current roles; exit 2 when there is none."""
    from app.accounts.handlers import resolve_account

    engine = make_engine(settings)
    instrument_sql(engine)
    anonymous = Caller(sessions=make_sessions(engine), settings=settings, origin="mcp")
    caller = resolve_account(anonymous, username)
    if caller.user_id is None:
        engine.dispose()
        raise SystemExit(2)
    return caller


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
