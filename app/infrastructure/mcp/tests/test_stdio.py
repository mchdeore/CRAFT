"""Actual MCP sessions and stdio subprocesses use the operation pipeline."""

import asyncio
import io
import json
import os
import subprocess
from uuid import uuid4

from fastapi import FastAPI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent
from sqlalchemy import Engine, select, text

from app.infrastructure.access import Caller
from app.infrastructure.logging import configure_logging
from app.infrastructure.mcp.server import build_server
from app.infrastructure.mcp.tests.fixtures import fixture_registry
from app.infrastructure.operations.registry import registry
from app.infrastructure.operations.storage import call_log


def server_fixture(app: FastAPI):
    local, executed = fixture_registry()
    caller = Caller(
        user_id=uuid4(),
        roles=frozenset({"member"}),
        sessions=app.state.sessions,
        settings=app.state.settings,
    )
    return build_server(local, caller), local, executed


def log_rows(app: FastAPI, name: str):
    with app.state.engine.connect() as connection:
        return (
            connection.execute(
                select(call_log).where(call_log.c.name.endswith("." + name)).order_by(call_log.c.id)
            )
            .mappings()
            .all()
        )


def test_lists_only_tool_operations(app: FastAPI) -> None:
    server, _, _ = server_fixture(app)

    async def run():
        async with create_connected_server_and_client_session(server) as session:
            result = await session.list_tools()
            assert {t.name for t in result.tools} == {"probe_read", "probe_write"}
            assert all(t.description and t.inputSchema["type"] == "object" for t in result.tools)

    asyncio.run(run())


def test_call_runs_pipeline(app: FastAPI) -> None:
    server, _, _ = server_fixture(app)
    before = len(log_rows(app, "probe_read"))

    async def run():
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool("probe_read", {"value": 7})
            assert result.structuredContent == {"value": 7, "resultValue": 14}
            assert len(result.content) == 1
            assert isinstance(result.content[0], TextContent)
            assert json.loads(result.content[0].text) == result.structuredContent

    asyncio.run(run())
    rows = log_rows(app, "probe_read")[before:]
    assert len(rows) == 1 and rows[0]["origin"] == "mcp" and rows[0]["outcome"] == "ok"


def test_invalid_arguments(app: FastAPI) -> None:
    server, _, _ = server_fixture(app)
    before = len(log_rows(app, "probe_read"))

    async def run():
        async with create_connected_server_and_client_session(server) as session:
            for arguments in [{"value": "7"}, {"value": 7, "unknown": True}]:
                result = await session.call_tool("probe_read", arguments)
                assert result.structuredContent is not None
                assert result.isError and result.structuredContent["code"] == "invalid_input"

    asyncio.run(run())
    rows = log_rows(app, "probe_read")[before:]
    assert len(rows) == 2 and all(r["outcome"] == "error" for r in rows)


def test_effectful_tool_refused(app: FastAPI) -> None:
    server, _, executed = server_fixture(app)

    async def run():
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool("probe_write", {"value": 7})
            assert result.structuredContent is not None
            assert result.isError and result.structuredContent["code"] == "approval_required"

    asyncio.run(run())
    assert not executed


def test_unknown_tool(app: FastAPI) -> None:
    server, _, _ = server_fixture(app)

    async def run():
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool("nope", {})
            assert result.structuredContent is not None
            assert result.isError and result.structuredContent["code"] == "not_found"

    asyncio.run(run())


def test_no_content_in_logs(app: FastAPI) -> None:
    server, _, _ = server_fixture(app)
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    marker = "CRAFT_SECRET_B05"

    async def run():
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool("probe_read", {"value": marker})
            assert result.isError

    asyncio.run(run())
    assert marker not in stream.getvalue()
    assert all(marker not in str(cell) for cell in log_rows(app, "probe_read")[-1].values())


def test_cli_unknown_account() -> None:
    result = subprocess.run(
        ["uv", "run", "craft", "mcp", "--as", "nobody"],
        env=os.environ | {"CRAFT_DATABASE_NAME": "craft_test"},
        input="",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and result.stdout == ""
    assert "unknown or disabled account" in result.stderr


def test_cli_lists_real_registry(owner_engine: Engine) -> None:
    with owner_engine.begin() as connection:
        user = connection.execute(
            text(
                "insert into users (username,display_name) values ('mcp-test','Test') returning id"
            )
        ).scalar_one()
        connection.execute(
            text("insert into user_roles (user_id,role) values (:id,'member')"), {"id": user}
        )

    async def run():
        parameters = StdioServerParameters(
            command="uv",
            args=["run", "craft", "mcp", "--as", "mcp-test"],
            env=os.environ | {"CRAFT_DATABASE_NAME": "craft_test"},
        )
        async with (
            stdio_client(parameters) as (incoming, outgoing),
            ClientSession(incoming, outgoing) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
            assert {t.name for t in result.tools} == {
                o.tool_name for o in registry.values() if o.tool
            }

    try:
        asyncio.run(run())
    finally:
        with owner_engine.begin() as connection:
            connection.execute(text("delete from user_roles where user_id=:id"), {"id": user})
            connection.execute(text("delete from users where id=:id"), {"id": user})
