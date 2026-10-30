"""Actual local HTTP/MCP clients prove current run authentication and operation parity."""

import asyncio
import base64
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import msgspec
import pytest
from agent_framework import MCPStreamableHTTPTool
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.agent import service
from app.agent.handlers import RunRef, issue_run_token, revoke_run_token
from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import NotFound
from app.infrastructure.mcp.client import client as native_tool
from app.infrastructure.operations import DATA, operation
from app.infrastructure.operations.registry import registry
from app.infrastructure.shapes import Shape
from app.library.handlers import DocumentRef, get_document
from app.tests.support.chat import chat as chat
from app.tests.support.chat import collect, request, row, wait_for
from app.tests.support.cube import cube_source as cube_source
from app.tests.support.mcp import session_stream


@pytest.fixture
def token(chat):
    data = request(chat)
    service.start(chat["caller"], data, "simulated")
    caller = replace(chat["caller"], conversation_id=data.thread_id, run_id=data.run_id)
    raw = issue_run_token(caller, RunRef(run=data.run_id)).token
    yield chat | {"data": data, "trusted": caller, "token": raw}
    revoke_run_token(caller, RunRef(run=data.run_id))
    service.finish(caller, data, "cancelled", {})


def rpc(token, header=None, cookie=None):
    headers = {"Accept": "application/json, text/event-stream"}
    if header is not None:
        headers["Authorization"] = header
    if cookie is not None:
        headers["Cookie"] = "__Host-craft_session=" + cookie
    with httpx.Client(trust_env=False) as client:
        return client.post(
            token["settings"].mcp_url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
        ).status_code


def test_mcp_lists_allowlisted_operations(token):
    async def execute():
        async with session_stream(token["settings"].mcp_url, token["token"]) as session:
            response = await session.list_tools()
            names = {tool.name for tool in response.tools}
            expected = {o.tool_name for o in registry.values() if o.tool}
            assert names == expected and len(names) == len(response.tools)
            assert all(tool.inputSchema.get("type") == "object" for tool in response.tools)
            assert not any(
                name in names
                for name in ["resolve_run_token", "load_for_run", "embed", "run_agent"]
            )
            for arguments in [{"query": 1}, {"query": "Peak power", "unknown": True}]:
                invalid = await session.call_tool("search", arguments)
                assert invalid.isError and invalid.structuredContent is not None
                assert invalid.structuredContent["code"] == "invalid_input"
        selected = native_tool(token["settings"].mcp_url, token["token"], [], allowed=["search"])
        assert isinstance(selected, MCPStreamableHTTPTool)
        try:
            await selected.connect()
            assert {function.name for function in selected.functions} == {"search"}
        finally:
            await selected.close()

    asyncio.run(execute())


def test_mcp_invalid_token_refused(token):
    assert all(
        rpc(token, header) == 401
        for header in [
            None,
            "Basic invalid",
            "Bearer invalid",
            "Bearer "
            + base64.urlsafe_b64encode(uuid4().bytes + uuid4().bytes).rstrip(b"=").decode(),
        ]
    )
    with TestClient(create_app(token["settings"]), base_url="https://testserver") as client:
        assert token["sign_in"](client).status_code == 200
        cookie = client.cookies.get("__Host-craft_session")
    assert rpc(token, cookie=cookie) == 401
    runs, users = m.metadata.tables["agent_runs"], m.metadata.tables["users"]
    with token["owner"].begin() as connection:
        connection.execute(
            runs.update()
            .where(runs.c.id == token["data"].run_id)
            .values(token_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert rpc(token, "Bearer " + token["token"]) == 401
    with token["owner"].begin() as connection:
        connection.execute(
            runs.update()
            .where(runs.c.id == token["data"].run_id)
            .values(token_expires_at=datetime.now(UTC) + timedelta(seconds=120))
        )
    for status in ["paused", "finished", "failed", "cancelled"]:
        with token["owner"].begin() as connection:
            connection.execute(
                runs.update().where(runs.c.id == token["data"].run_id).values(status=status)
            )
        assert rpc(token, "Bearer " + token["token"]) == 401
    with token["owner"].begin() as connection:
        connection.execute(
            runs.update().where(runs.c.id == token["data"].run_id).values(status="running")
        )
        connection.execute(
            users.update()
            .where(users.c.id == token["trusted"].user_id)
            .values(disabled_at=datetime.now(UTC))
        )
    try:
        assert rpc(token, "Bearer " + token["token"]) == 401
    finally:
        with token["owner"].begin() as connection:
            connection.execute(
                users.update()
                .where(users.c.id == token["trusted"].user_id)
                .values(disabled_at=None)
            )
    conversations = m.metadata.tables["conversations"]
    with token["owner"].begin() as connection:
        connection.execute(
            conversations.update()
            .where(conversations.c.id == token["data"].thread_id)
            .values(archived_at=datetime.now(UTC))
        )
    try:
        assert rpc(token, "Bearer " + token["token"]) == 401
    finally:
        with token["owner"].begin() as connection:
            connection.execute(
                conversations.update()
                .where(conversations.c.id == token["data"].thread_id)
                .values(archived_at=None)
            )
    with token["owner"].begin() as connection:
        other = connection.execute(
            users.insert()
            .values(
                username="simulated_owner_" + uuid4().hex, display_name="Synthetic alternate owner"
            )
            .returning(users.c.id)
        ).scalar_one()
        connection.execute(
            conversations.update()
            .where(conversations.c.id == token["data"].thread_id)
            .values(owner_id=other)
        )
    try:
        assert rpc(token, "Bearer " + token["token"]) == 401
    finally:
        with token["owner"].begin() as connection:
            connection.execute(
                conversations.update()
                .where(conversations.c.id == token["data"].thread_id)
                .values(owner_id=token["trusted"].user_id)
            )
            connection.execute(users.delete().where(users.c.id == other))
    revoke_run_token(token["trusted"], RunRef(run=token["data"].run_id))
    assert rpc(token, "Bearer " + token["token"]) == 401


def test_mcp_token_digest_and_revocation(token, capsys):
    raw = base64.urlsafe_b64decode(token["token"] + "=")
    stored = row(token, token["data"].run_id)
    assert len(raw) == 32 and stored["token_sha256"] == hashlib.sha256(raw).digest()
    assert stored["token_sha256"] != raw
    replacement = issue_run_token(token["trusted"], RunRef(run=token["data"].run_id)).token
    assert replacement != token["token"] and rpc(token, "Bearer " + token["token"]) == 401
    service.finish(token["trusted"], token["data"], "paused", {})
    assert row(token, token["data"].run_id)["token_sha256"] is None
    assert rpc(token, "Bearer " + replacement) == 401
    captured = capsys.readouterr()
    safe = (
        token["token"] not in captured.out + captured.err
        and replacement not in captured.out + captured.err
    )
    assert safe


def test_mcp_http_python_permissions_and_logging_agree(token):
    identifier = token["item"]
    before = set()
    logs = m.metadata.tables["call_log"]
    with token["owner"].connect() as connection:
        before = set(
            connection.execute(
                select(logs.c.call_id).where(logs.c.name == "library.get_document")
            ).scalars()
        )
    python = get_document(token["trusted"], DocumentRef(item=identifier))
    with TestClient(create_app(token["settings"]), base_url="https://testserver") as client:
        assert token["sign_in"](client).status_code == 200
        http = client.get("/api/library/documents/" + identifier)

    forged = str(uuid4())

    async def read():
        async with session_stream(token["settings"].mcp_url, token["token"]) as session:
            return await session.call_tool(
                "get_document",
                {"item": identifier},
                meta={
                    "craft/toolCallId": "simulated_read",
                    "craft/userId": forged,
                    "craft/runId": forged,
                    "craft/parentCallId": forged,
                    "craft/requestId": forged,
                },
            )

    result = asyncio.run(read())
    matched = (
        http.status_code == 200
        and not result.isError
        and result.structuredContent == msgspec.to_builtins(python) == http.json()
    )
    assert matched
    with token["owner"].connect() as connection:
        rows = (
            connection.execute(
                select(logs).where(
                    logs.c.name == "library.get_document", logs.c.call_id.not_in(before)
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 3 and {r["origin"] for r in rows} == {"http", "mcp", "python"}
    assert all(
        str(r[key]) != forged
        for r in rows
        if r["origin"] == "mcp"
        for key in ["user_id", "run_id", "parent_call_id", "request_id"]
    )
    grants = m.mission_grants
    with token["owner"].begin() as connection:
        connection.execute(grants.delete().where(grants.c.user_id == token["trusted"].user_id))
    with pytest.raises(NotFound):
        get_document(token["trusted"], DocumentRef(item=identifier))
    with TestClient(create_app(token["settings"]), base_url="https://testserver") as client:
        assert token["sign_in"](client).status_code == 200
        assert client.get("/api/library/documents/" + identifier).status_code == 404
    denied = asyncio.run(read())
    assert denied.isError and denied.structuredContent is not None
    assert denied.structuredContent["code"] == "not_found"
    with token["owner"].connect() as connection:
        rejected = (
            connection.execute(
                select(logs).where(
                    logs.c.name == "library.get_document",
                    logs.c.call_id.not_in(before),
                    logs.c.outcome == "error",
                )
            )
            .mappings()
            .all()
        )
    assert len(rejected) == 3 and {r["origin"] for r in rejected} == {"http", "mcp", "python"}
    assert all(r["error_code"] == "not_found" for r in rejected)


def test_mcp_log_links_run_tool_call_operation(chat, capsys):
    data = request(chat, "Peak power " + uuid4().hex)
    marker = data.messages[0]["content"]
    events = collect(chat["caller"], data)
    assert events[-1]["type"] == "RUN_FINISHED"
    identifier = next(e["toolCallId"] for e in events if e["type"] == "TOOL_CALL_START")
    logs = m.metadata.tables["call_log"]

    def logged():
        with chat["owner"].connect() as connection:
            return (
                connection.execute(select(logs).where(logs.c.run_id == data.run_id))
                .mappings()
                .all()
            )

    # The worker logs its own operation after it has written RUN_FINISHED.
    wait_for(lambda: any(r["name"] == "agent.agent_run" for r in logged()))
    records = logged()
    root = next(r for r in records if r["name"] == "agent.run_agent")
    worker = next(r for r in records if r["name"] == "agent.agent_run")
    assert worker["parent_call_id"] == root["call_id"]
    root = worker  # tool calls run under the agent worker's run
    tool = next(r for r in records if r["name"] == "library.search")
    assert tool["origin"] == "mcp" and tool["user_id"] == chat["caller"].user_id
    assert tool["conversation_id"] == data.thread_id and tool["tool_call_id"] == identifier
    assert tool["parent_call_id"] == root["call_id"] and tool["request_id"] == root["request_id"]
    assert any(
        r["kind"] == "step"
        and r["name"] == "agent.tool"
        and r["parent_call_id"] == root["call_id"]
        and r["tool_call_id"] == identifier
        for r in records
    )
    assert any(
        r["name"] == "agent.model" and r["parent_call_id"] == root["call_id"] for r in records
    )
    assert row(chat, data.run_id)["token_sha256"] is None
    captured = capsys.readouterr()
    safe = marker not in captured.out + captured.err
    assert safe


def test_mcp_effectful_tool_without_approval_refused(token):
    executed = []

    class EffectInput(Shape):
        value: int

    @operation(PUBLIC, post="/mcp-effect-probe", tool=True, effects=DATA)
    def attempt_effect(caller: Caller, request: EffectInput) -> None:
        """Attempt one simulated effect; use to test refusal, for example value1."""
        executed.append(True)

    try:

        async def execute():
            selected = native_tool(token["settings"].mcp_url, token["token"], [])
            try:
                await selected.connect()
                names = {function.name for function in selected.functions}
                assert "search" in names
                assert not {"attempt_effect", "ask_agent"}.intersection(names)
            finally:
                await selected.close()
            async with session_stream(token["settings"].mcp_url, token["token"]) as session:
                listed = await session.list_tools()
                assert "attempt_effect" in {tool.name for tool in listed.tools}
                return await session.call_tool(
                    "attempt_effect", {"value": 1}, meta={"craft/toolCallId": "simulated_effect"}
                )

        result = asyncio.run(execute())
        assert result.isError and result.structuredContent is not None
        assert result.structuredContent["code"] == "approval_required"
        assert not executed
    finally:
        registry.pop(attempt_effect.__operation__.name)
