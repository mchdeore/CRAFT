"""MCP clients of the live test server: an SDK session, and raw JSON-RPC with tokens."""

from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from app import create_app
from app.infrastructure.database import metadata as m
from app.infrastructure.operations.storage import call_log

tokens = m.metadata.tables["workspace_tokens"]


@asynccontextmanager
async def session_stream(url, raw):
    async with httpx.AsyncClient(
        headers={"Authorization": "Bearer " + raw}, trust_env=False, follow_redirects=True
    ) as client:
        async with streamable_http_client(url, http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


@pytest.fixture
def account(chat):
    """The chat account's browser session, which issues its tokens."""
    app = create_app(chat["settings"])
    with (
        TestClient(
            app, base_url="https://testserver", headers={"Origin": "https://testserver"}
        ) as browser,
        TestClient(app, base_url="https://testserver") as python,
    ):
        chat["sign_in"](browser)

        def issue(kind="mcp", **body):
            response = browser.post(
                "/api/me/tokens", json={"label": "client", "kind": kind, **body}
            )
            assert response.status_code == 200, response.text
            return response.json()

        yield chat | {
            "browser": browser,
            "python": python,
            "issue": issue,
            "user": chat["caller"].user_id,
        }
    with chat["owner"].begin() as connection:
        connection.execute(call_log.delete().where(call_log.c.user_id == chat["caller"].user_id))
        connection.execute(tokens.delete().where(tokens.c.user_id == chat["caller"].user_id))


def post(account, raw, method="tools/list", params=None, **headers):
    """One JSON-RPC message to /mcp, as a client without the SDK sends it."""
    sent = {"Accept": "application/json, text/event-stream", **headers}
    if raw is not None:
        sent["Authorization"] = "Bearer " + raw
    with httpx.Client(trust_env=False) as client:
        return client.post(
            account["settings"].mcp_url,
            headers=sent,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        )


def call(account, raw, name, arguments=None, **headers):
    return post(account, raw, "tools/call", {"name": name, "arguments": arguments or {}}, **headers)


def logged(account, **where):
    with account["owner"].connect() as connection:
        return (
            connection.execute(
                select(call_log)
                .where(*[call_log.c[name] == value for name, value in where.items()])
                .order_by(call_log.c.id)
            )
            .mappings()
            .all()
        )
