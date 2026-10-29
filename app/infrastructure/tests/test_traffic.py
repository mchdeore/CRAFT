"""One traffic row per request with body byte counts; never paths, queries, tokens or content."""

import io
import time
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import anyio
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.responses import FileResponse, JSONResponse, StreamingResponse

from app import create_app
from app.accounts import service, storage
from app.agent import service as agent_service
from app.agent.handlers import RunRef, issue_run_token, revoke_run_token
from app.agent.tests.test_chat_stream import request as run_request
from app.infrastructure import traffic
from app.infrastructure.logging import configure_logging
from app.infrastructure.operations.storage import call_log
from app.infrastructure.requests import RequestIdMiddleware
from app.infrastructure.shapes import RawFile
from app.infrastructure.tests.test_grants import assert_grants
from app.infrastructure.traffic import TrafficMiddleware, traffic_log
from app.tests.support.chat import chat as chat
from app.tests.support.cube import cube_source as cube_source

PASSWORD = "synthetic-traffic-password"
ORIGIN = "http://localhost:8000"


@pytest.fixture
def recorded(monkeypatch) -> list[traffic.Traffic]:
    rows: list[traffic.Traffic] = []
    monkeypatch.setattr(traffic, "record", lambda sessions, row: rows.append(row))
    return rows


def counting_app(tmp_path) -> FastAPI:
    app = FastAPI()
    download = tmp_path / "download.bin"
    download.write_bytes(b"d" * 200_000)  # several chunks

    @app.get("/json")
    def json_route() -> JSONResponse:
        return JSONResponse({"value": "j" * 100})

    @app.post("/upload")
    async def upload(request: Request) -> JSONResponse:
        return JSONResponse({"size": len(await request.body())})

    @app.get("/download")
    def download_route() -> FileResponse:
        return FileResponse(download)

    @app.get("/events")
    def events() -> StreamingResponse:
        async def source():
            for index in range(3):
                yield f"data: {index}\n\n".encode()

        return StreamingResponse(source(), media_type="text/event-stream")

    @app.get("/broken-events")
    def broken_events() -> StreamingResponse:
        async def source():
            yield b"data: first\n\n"
            raise RuntimeError("never in a row")

        return StreamingResponse(source(), media_type="text/event-stream")

    @app.post("/refuse")
    def refuse() -> JSONResponse:  # answers without reading the body
        return JSONResponse({"code": "too_large"}, status_code=413)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("never in a row")

    @app.get("/health/live")
    def live() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(TrafficMiddleware, sessions=sessionmaker())
    return app


def test_body_bytes_are_counted_for_every_kind_of_response(tmp_path, recorded) -> None:
    upload = b"u" * (5 * 1024 * 1024)
    with TestClient(counting_app(tmp_path), raise_server_exceptions=False) as client:
        responses = [
            client.get("/json"),
            client.post("/upload", content=upload),
            client.get("/download"),
            client.get("/events"),
            client.get("/broken-events"),
            client.post("/refuse", content=upload),
            client.get("/boom"),
            client.get("/nope"),
            client.get("/health/live"),
        ]
    assert len(recorded) == 8  # health is not counted
    for index, (response, row) in enumerate(zip(responses, recorded, strict=False)):
        assert row.request_id == UUID(response.headers["x-request-id"])
        assert row.status == response.status_code
        if index != 4:  # TestClient drops the partial body of a failed stream
            assert row.bytes_out == len(response.content)
        assert row.operation == "unmatched" and row.origin is None and row.user_id is None
    # Only the body the app read counts: /refuse answers before reading its upload.
    assert [row.bytes_in for row in recorded] == [0, len(upload), 0, 0, 0, 0, 0, 0]
    assert recorded[2].bytes_out == 200_000
    assert recorded[4].bytes_out == len(b"data: first\n\n")  # the stream failed after one event
    assert recorded[6].status == 500  # the last-resort body from RequestIdMiddleware


def test_a_stream_dropped_or_cancelled_midway_still_writes_one_row(recorded) -> None:
    async def endless():
        while True:
            yield b"data: tick\n\n"
            await anyio.sleep(0)

    async def drive(cancel: bool) -> int:
        sent = 0
        disconnected = anyio.Event()

        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal sent
            sent += len(message.get("body", b""))
            if sent >= 36:
                if cancel:
                    scope.cancel()
                disconnected.set()

        middleware = TrafficMiddleware(
            StreamingResponse(endless(), media_type="text/event-stream"), sessions=sessionmaker()
        )
        http = {"type": "http", "method": "GET", "path": "/events", "headers": []}
        with anyio.CancelScope() as scope:
            await middleware(dict(http, asgi={"spec_version": "2.3"}), receive, send)
        return sent

    for cancel in (False, True):
        recorded.clear()
        sent = anyio.run(drive, cancel)
        assert len(recorded) == 1
        assert recorded[0].bytes_out == sent > 0
        assert recorded[0].status == 200


@pytest.fixture
def signed_in(settings, owner_engine):
    username = "traffic_" + uuid4().hex
    with owner_engine.begin() as connection:
        user = connection.execute(
            storage.users.insert()
            .values(username=username, display_name="Test")
            .returning(storage.users.c.id)
        ).scalar_one()
        connection.execute(
            storage.local_credentials.insert().values(
                user_id=user, password_hash=service.PASSWORDS.hash(PASSWORD)
            )
        )
        connection.execute(storage.user_roles.insert().values(user_id=user, role="member"))
    service.ATTEMPTS.clear()
    with TestClient(
        create_app(settings), base_url="https://testserver", headers={"Origin": ORIGIN}
    ) as browser:
        response = browser.post("/api/session", json={"username": username, "password": PASSWORD})
        assert response.status_code == 200
        yield SimpleNamespace(id=user, browser=browser)
    with owner_engine.begin() as connection:
        connection.execute(traffic_log.delete().where(traffic_log.c.user_id == user))
        connection.execute(call_log.delete().where(call_log.c.user_id == user))
        for table in [
            storage.workspace_tokens,
            storage.auth_sessions,
            storage.local_credentials,
            storage.user_roles,
        ]:
            connection.execute(table.delete().where(table.c.user_id == user))
        connection.execute(storage.users.delete().where(storage.users.c.id == user))


def rows_for(owner_engine, response) -> list[dict]:
    request_id = UUID(response.headers["x-request-id"])
    with owner_engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(traffic_log).where(traffic_log.c.request_id == request_id)
            ).mappings()
        ]


def settled_rows_for(owner_engine, response, seconds: float = 5.0) -> list[dict]:
    """A live server writes the row after the response is sent, so the client can read first."""
    deadline = time.monotonic() + seconds
    while not (rows := rows_for(owner_engine, response)) and time.monotonic() < deadline:
        time.sleep(0.02)
    return rows


def test_operations_record_their_name_and_caller(signed_in, owner_engine) -> None:
    secret = "SECRET-" + uuid4().hex
    me = signed_in.browser.get("/api/me")
    refused = signed_in.browser.get("/api/me?probe=" + secret)  # accounts.me takes no input
    token = signed_in.browser.post(
        "/api/me/workspace-tokens", json={"label": "notebook", "hours": 1}
    ).json()
    signed_in.browser.cookies.clear()  # a workspace token never combines with a session
    bearer = {"Authorization": "Bearer " + token["token"]}
    workspace = signed_in.browser.get("/api/me", headers=bearer)
    anonymous = signed_in.browser.get("/api/me")
    mcp = signed_in.browser.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    health = signed_in.browser.get("/health/live")

    [row] = rows_for(owner_engine, me)
    assert row["operation"] == "accounts.me" and row["method"] == "GET"
    assert (row["origin"], row["user_id"], row["status"]) == ("http", signed_in.id, 200)
    assert row["bytes_out"] == len(me.content) and row["bytes_in"] == 0

    [row] = rows_for(owner_engine, refused)
    assert (row["operation"], row["user_id"], row["status"]) == ("accounts.me", signed_in.id, 400)
    assert row["bytes_out"] == len(refused.content)
    assert secret not in str(row)

    [row] = rows_for(owner_engine, workspace)
    assert (row["origin"], row["user_id"]) == ("workspace", signed_in.id)
    assert row["workspace_token_id"] == UUID(token["id"])
    assert token["token"] not in str(row)

    [row] = rows_for(owner_engine, anonymous)
    assert (row["operation"], row["origin"], row["user_id"], row["status"]) == (
        "accounts.me",
        "http",
        None,
        401,
    )

    [row] = rows_for(owner_engine, mcp)
    assert (row["operation"], row["status"]) == ("mcp", 401)
    assert row["bytes_out"] == len(mcp.content)

    assert rows_for(owner_engine, health) == []


def test_refused_requests_are_counted(signed_in, owner_engine) -> None:
    token = signed_in.browser.post(
        "/api/me/workspace-tokens", json={"label": "notebook", "hours": 1}
    ).json()
    oversized = b"x" * (RawFile.max_bytes + 1)
    too_large = signed_in.browser.post(
        "/api/summarize/upload",
        content=oversized,
        headers={"Content-Type": "application/pdf", "X-File-Name": "large.pdf"},
    )
    signed_in.browser.cookies.clear()
    denied = signed_in.browser.post(  # workspace tokens may not issue workspace tokens
        "/api/me/workspace-tokens",
        json={"label": "again"},
        headers={"Authorization": "Bearer " + token["token"]},
    )

    [row] = rows_for(owner_engine, too_large)
    assert (row["operation"], row["user_id"], row["status"]) == (
        "summarize.upload",
        signed_in.id,
        413,
    )
    assert row["bytes_in"] == len(oversized) and row["bytes_out"] == len(too_large.content)

    [row] = rows_for(owner_engine, denied)
    assert (row["operation"], row["origin"], row["status"]) == (
        "accounts.create_workspace_token",
        "workspace",
        403,
    )
    assert (row["user_id"], row["workspace_token_id"]) == (signed_in.id, UUID(token["id"]))


@pytest.fixture
def run_token(chat):
    data = run_request(chat)
    agent_service.start(chat["caller"], data, "simulated")
    caller = replace(chat["caller"], conversation_id=data.thread_id, run_id=data.run_id)
    raw = issue_run_token(caller, RunRef(run=data.run_id)).token
    yield chat | {"trusted": caller, "token": raw}
    revoke_run_token(caller, RunRef(run=data.run_id))
    agent_service.finish(caller, data, "cancelled", {})


def test_each_mcp_request_is_one_row_joined_to_its_calls(run_token) -> None:
    """A run's tool calls arrive as separate /mcp requests; each is counted on its own and
    joins call_log by request_id (the token check), and from there by run_id."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer " + run_token["token"],
    }
    calls = [
        {"method": "tools/list", "params": {}},
        {"method": "tools/call", "params": {"name": "no_such_tool", "arguments": {}}},
        {"method": "tools/list", "params": {}},
    ]
    with httpx.Client(trust_env=False) as client:
        responses = [
            client.post(
                run_token["settings"].mcp_url, headers=headers, json={"jsonrpc": "2.0", "id": i} | c
            )
            for i, c in enumerate(calls)
        ]
    assert [response.status_code for response in responses] == [200, 200, 200]
    owner, trusted = run_token["owner"], run_token["trusted"]
    for response in responses:
        [row] = settled_rows_for(owner, response)
        assert (row["operation"], row["origin"], row["user_id"], row["status"]) == (
            "mcp",
            "mcp",
            trusted.user_id,
            200,
        )
        assert row["bytes_in"] == len(response.request.content)
        assert row["bytes_out"] == len(response.content)
        assert run_token["token"] not in str(row)
        with owner.connect() as connection:
            runs = connection.execute(
                select(call_log.c.run_id).where(call_log.c.request_id == row["request_id"])
            ).scalars()
            assert set(runs) == {trusted.run_id}


def test_a_no_op_record_writes_nothing_and_changes_nothing(
    signed_in, owner_engine, monkeypatch
) -> None:
    monkeypatch.setattr(traffic, "record", lambda sessions, row: None)
    response = signed_in.browser.get("/api/me")
    assert response.status_code == 200 and response.json()["username"].startswith("traffic_")
    assert rows_for(owner_engine, response) == []


def test_a_failed_record_never_touches_the_response(signed_in, monkeypatch) -> None:
    def broken(sessions, row):
        raise RuntimeError("database down")

    monkeypatch.setattr(traffic, "record", broken)
    assert signed_in.browser.get("/api/me").status_code == 200


def test_no_password_token_or_content_reaches_rows_logs_or_error_bodies(
    signed_in, owner_engine, monkeypatch
) -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream)
    content = "CONTENT-" + uuid4().hex
    token = signed_in.browser.post(
        "/api/me/workspace-tokens", json={"label": "notebook", "hours": 1}
    ).json()
    bad = signed_in.browser.post(
        "/api/me/workspace-tokens?note=" + content, json={"label": content, "hours": "x" + content}
    )
    signed_in.browser.cookies.clear()
    signed_in.browser.headers["Authorization"] = "Bearer " + token["token"]
    workspace = signed_in.browser.get("/api/me")
    signed_in.browser.headers["Authorization"] = "Bearer " + token["token"][::-1]
    forged = signed_in.browser.get("/api/me")
    del signed_in.browser.headers["Authorization"]
    retry = signed_in.browser.post("/api/session", json={"username": "x", "password": PASSWORD})

    def broken(sessions, row):
        raise RuntimeError("cannot store " + content + " " + token["token"])

    monkeypatch.setattr(traffic, "record", broken)
    failed = signed_in.browser.get("/api/me?probe=" + content)

    assert [r.status_code for r in (bad, workspace, forged, retry, failed)] == [
        400,
        200,
        401,
        401,
        401,
    ]
    secrets = (PASSWORD, token["token"], content)
    output = stream.getvalue()
    assert "traffic_record_failed" in output and '"error_type":"RuntimeError"' in output
    for response in (bad, workspace, forged, retry, failed):
        assert not any(secret in response.text for secret in secrets)
        for row in rows_for(owner_engine, response):
            assert not any(secret in str(row) for secret in secrets)
    assert not any(secret in output for secret in secrets)


def test_the_app_can_only_append(owner_engine) -> None:
    assert_grants(owner_engine, {"traffic_log": {"INSERT"}})
