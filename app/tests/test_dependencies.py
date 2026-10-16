"""The agreed pins work together in one environment, before anything depends on them."""

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import argon2
import mcp.types as types
import msgspec
import pgvector.sqlalchemy
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from app.infrastructure.shapes import Shape


class Probe(Shape):
    title: str


def check_msgspec_is_strict() -> None:
    assert msgspec.json.decode(b'{"title":"x"}', type=Probe).title == "x"
    for bad in (b'{"title":1}', b'{"title":"x","extra":1}'):
        with pytest.raises(msgspec.ValidationError):
            msgspec.json.decode(bad, type=Probe)


def check_mcp_mounts_stateless_in_the_same_app() -> None:
    server = Server("craft")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(name="probe", description="Probe.", inputSchema={"type": "object"})]

    manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with manager.run():
            yield

    app = FastAPI(openapi_url=None, lifespan=lifespan)
    app.mount("/mcp", manager.handle_request)
    headers = {
        "accept": "application/json, text/event-stream",
        "mcp-protocol-version": "2025-06-18",
    }
    with TestClient(app) as client:
        listed = client.post("/mcp/", headers=headers,
                             json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})  # fmt: skip
    assert [tool["name"] for tool in listed.json()["result"]["tools"]] == ["probe"]


def check_agent_framework_classes_we_use() -> None:
    import agent_framework as af
    import agent_framework_ag_ui as ag_ui
    from agent_framework.foundry import FoundryChatClient

    assert af.Agent and af.AgentSession and FoundryChatClient
    params = inspect.signature(af.MCPStreamableHTTPTool).parameters
    assert {"header_provider", "allowed_tools", "approval_mode"} <= set(params)
    assert hasattr(ag_ui, "add_agent_framework_fastapi_endpoint")


def check_database_and_password_libraries() -> None:
    assert pgvector.sqlalchemy.Vector(3)
    assert argon2.PasswordHasher().hash("x" * 12).startswith("$argon2id$")


def test_dependencies_work_together() -> None:
    check_msgspec_is_strict()
    check_mcp_mounts_stateless_in_the_same_app()
    check_agent_framework_classes_we_use()
    check_database_and_password_libraries()
