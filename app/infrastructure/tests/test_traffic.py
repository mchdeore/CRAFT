"""One traffic row per request with body byte counts; never paths, queries, tokens or content."""

from types import SimpleNamespace
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.responses import FileResponse, JSONResponse, StreamingResponse

from app import create_app
from app.accounts import service, storage
from app.infrastructure import traffic
from app.infrastructure.operations.storage import call_log
from app.infrastructure.requests import RequestIdMiddleware
from app.infrastructure.tests.test_grants import assert_grants
from app.infrastructure.traffic import TrafficMiddleware, traffic_log

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
    upload = b"u" * 70_000
    with TestClient(counting_app(tmp_path), raise_server_exceptions=False) as client:
        responses = [
            client.get("/json"),
            client.post("/upload", content=upload),
            client.get("/download"),
            client.get("/events"),
            client.get("/boom"),
            client.get("/nope"),
            client.get("/health/live"),
        ]
    assert len(recorded) == 6  # health is not counted
    for response, row in zip(responses, recorded, strict=False):
        assert row.request_id == UUID(response.headers["x-request-id"])
        assert row.status == response.status_code
        assert row.bytes_out == len(response.content)
        assert row.operation == "unmatched" and row.origin is None and row.user_id is None
    assert [row.bytes_in for row in recorded] == [0, len(upload), 0, 0, 0, 0]
    assert recorded[2].bytes_out == 200_000
    assert recorded[4].status == 500  # the last-resort body from RequestIdMiddleware


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


def test_operations_record_their_name_and_caller(signed_in, owner_engine) -> None:
    secret = "SECRET-" + uuid4().hex
    me = signed_in.browser.get("/api/me?probe=" + secret)
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
    assert secret not in str(row) and token["token"] not in str(row)

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


def test_the_app_can_only_append(owner_engine) -> None:
    assert_grants(owner_engine, {"traffic_log": {"INSERT"}})
