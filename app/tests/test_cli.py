"""CLI and real MCP sessions read the same simulated library."""

import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import msgspec
import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app import create_app
from app.infrastructure.access import Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.provider.simulated import SimulatedProvider
from app.seed.base import seed
from app.tests.support import rows


@pytest.mark.slow
def test_cli_call_and_mcp(settings: Settings, tmp_path: Path) -> None:
    configured = msgspec.structs.replace(settings, provider_root=str(tmp_path))
    seed(configured)
    owner, engine = make_owner_sessions(configured)
    rows.load(engine, SimulatedProvider(tmp_path))
    engine.dispose()
    env = os.environ | {
        "CRAFT_DATABASE_NAME": settings.database_name,
        "CRAFT_PROVIDER_ROOT": str(tmp_path),
    }
    result = subprocess.run(
        [
            "uv",
            "run",
            "craft",
            "call",
            "library.list_documents",
            "--as",
            "analyst",
            "{}",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    # Search embeds its query with the configured model; the catalog needs no model.
    documents = json.loads(result.stdout)["documents"]
    assert documents and documents[0]["item"].startswith("itm_")

    async def run():
        parameters = StdioServerParameters(
            command="uv", args=["run", "craft", "mcp", "--as", "analyst"], env=env
        )
        async with (
            stdio_client(parameters) as (incoming, outgoing),
            ClientSession(incoming, outgoing) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert {"search", "get_document", "read_chunks", "explore"} <= {
                t.name for t in tools.tools
            }
            result = await session.call_tool("list_documents", {})
            assert result.structuredContent is not None and result.structuredContent["documents"]

    try:
        asyncio.run(run())
        with TestClient(create_app(configured)) as client:
            assert (
                client.post("/api/library/search", json={"query": "radiator margin"}).status_code
                == 401
            )
    finally:
        sessions, engine = make_owner_sessions(configured)
        with sessions.begin() as session:
            session.execute(
                m.vectors.delete().where(m.vectors.c.owner_kind.in_(["chunk", "doc_version"]))
            )
            session.execute(m.version_diffs.delete())
            session.execute(m.doc_versions.delete())
            session.execute(m.provider_items.delete())
            session.execute(m.index_cursor.delete())
        engine.dispose()


@pytest.mark.parametrize("arguments", [[], ['{"value": 2}']])
def test_cli_call_awaits_async_operations(monkeypatch, capsys, arguments) -> None:
    """An async operation's result is printed, with or without an input shape."""
    from app import cli
    from app.infrastructure.mcp import stdio
    from app.infrastructure.operations.registry import registry

    class Doubled(msgspec.Struct):
        value: int

    async def without_input(caller):
        return {"value": 1}

    async def with_input(caller, request):
        return {"value": request["value"] * 2}

    function, shape = (with_input, Doubled) if arguments else (without_input, None)
    monkeypatch.setitem(
        registry, "generated.async", SimpleNamespace(function=function, input=shape)
    )
    monkeypatch.setattr(stdio, "local_caller", lambda *_: Caller(user_id=uuid4()))
    cli.main(["call", "generated.async", "--as", "generated", *arguments])
    assert json.loads(capsys.readouterr().out) == {"value": 4 if arguments else 1}
