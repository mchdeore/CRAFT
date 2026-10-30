"""CLI and real MCP sessions read the same simulated library."""

import asyncio
import json
import os
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import msgspec
import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app import create_app
from app.infrastructure.access import Caller
from app.tests.support.cube import cube_source as cube_source


@pytest.mark.slow
def test_cli_call_and_mcp(cube_source) -> None:
    configured, username = cube_source["settings"], cube_source["username"]
    env = os.environ | {
        "CRAFT_DATABASE_NAME": configured.database_name,
        "CRAFT_PROVIDER_ROOT": configured.provider_root,
    }
    result = subprocess.run(
        [
            "uv",
            "run",
            "craft",
            "call",
            "library.list_documents",
            "--as",
            username,
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
            command="uv", args=["run", "craft", "mcp", "--as", username], env=env
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

    asyncio.run(run())
    with TestClient(create_app(configured)) as client:
        assert (
            client.post("/api/library/search", json={"query": "radiator margin"}).status_code == 401
        )


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
