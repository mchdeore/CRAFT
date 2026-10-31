"""Real HTTP and Python exercise the same declared pipeline."""

import json
import socket
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import msgspec
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import create_app
from app.infrastructure.access import PUBLIC, SIGNED_IN, Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import ApiError, Forbidden, InvalidInput
from app.infrastructure.operations import DATA, operation
from app.infrastructure.operations.context import current_call
from app.infrastructure.operations.registry import registry
from app.infrastructure.shapes import Shape
from app.tests.support.operations import ProbeInput, rows, trusted


class ProbeOutput(Shape):
    value: int
    result_value: int


class ProbeId(Shape):
    id: UUID


def test_operation_python_http_same_function(settings: Settings, last_call_id: int) -> None:
    @operation(PUBLIC, get="/probe")
    def probe(caller: Caller, request: ProbeInput) -> ProbeOutput:
        return ProbeOutput(value=request.value, result_value=request.value + 1)

    app = create_app(settings)
    caller = trusted(app)
    result = probe(caller, ProbeInput(value=7))
    assert result.value == 7 and result.result_value == 8
    with TestClient(app, headers={"Origin": "http://localhost:8000"}) as client:
        response = client.get("/api/probe?value=7")
    assert response.json() == {"value": 7, "resultValue": 8}
    records = rows(app, "probe.probe", last_call_id)
    assert len(records) == 2 and {r["origin"] for r in records} == {"python", "http"}


def test_operation_validation_is_strict(settings: Settings) -> None:
    @operation(PUBLIC, post="/probe")
    def probe(caller: Caller, request: ProbeInput) -> ProbeOutput:
        return ProbeOutput(value=request.value, result_value=request.value)

    @operation(PUBLIC, get="/probe/{id}")
    def by_id(caller: Caller, request: ProbeId) -> None:
        return None

    app = create_app(settings)
    caller = trusted(app)
    with TestClient(app, headers={"Origin": "http://localhost:8000"}) as client:
        assert client.post("/api/probe", json={"value": 7}).status_code == 200
        for body in [{"value": "7"}, {"value": 7, "unknown": True}]:
            response = client.post("/api/probe", json=body)
            assert response.status_code == 400 and response.json()["fields"]
            cache_policy = response.headers.get("Cache-Control")
            assert cache_policy == "no-store"
            with pytest.raises(InvalidInput):
                probe(caller, body)
        assert client.post("/api/probe", content=b"{").json()["code"] == "invalid_input"
        assert client.get("/api/probe/not-a-uuid").json()["code"] == "invalid_input"


def test_json_bodies_stop_at_four_mib(settings: Settings) -> None:
    """A body past 4 MiB is refused (413): unread when its Content-Length says so, else as it
    streams. Exactly 4 MiB is read. An operation without input still takes no body and refuses
    a small one as input."""

    @operation(PUBLIC, post="/body-probe")
    def body(caller: Caller, request: ProbeInput) -> None:
        return None

    @operation(PUBLIC, post="/no-input-probe")
    def no_input(caller: Caller) -> None:
        return None

    limit = 4 * 1024 * 1024
    full = b'{"value": 7' + b" " * (limit - 12) + b"}"
    oversized = b'{"username": "' + b"x" * 5 * 1024 * 1024 + b'", "password": "x"}'
    streamed = (oversized[at : at + 65536] for at in range(0, len(oversized), 65536))
    app = create_app(settings)
    with TestClient(app, headers={"Origin": "http://localhost:8000"}) as client:
        assert client.post("/api/body-probe", content=full).status_code == 204
        assert client.post("/api/body-probe", content=full + b" ").status_code == 413
        assert client.post("/api/session", content=oversized).status_code == 413
        assert client.post("/api/session", content=streamed).status_code == 413
        declared = {"Content-Length": str(5 * 1024 * 1024)}
        assert client.post("/api/session", content=b"{}", headers=declared).status_code == 413
        assert client.post("/api/no-input-probe").status_code == 204
        assert client.post("/api/no-input-probe", content=b"{}").status_code == 400
        assert client.post("/api/no-input-probe", content=oversized).status_code == 413
    app.state.engine.dispose()


def test_operation_curl_without_session(
    settings: Settings, tmp_path: Path, last_call_id: int
) -> None:
    @operation(SIGNED_IN, get="/probe")
    def probe(caller: Caller) -> None:
        return None

    app = create_app(settings)
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        headers = tmp_path / "headers.txt"
        body = tmp_path / "body.json"
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "-D",
                str(headers),
                "-o",
                str(body),
                "-w",
                "%{http_code}",
                f"http://127.0.0.1:{port}/api/probe",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout == "401"
        payload = json.loads(body.read_text())
        assert payload["code"] == "not_signed_in"
        request_id = next(
            line.partition(":")[2].strip()
            for line in headers.read_text().splitlines()
            if line.lower().startswith("x-request-id:")
        )
        assert payload["requestId"] == request_id
        recorded = rows(app, "probe.probe", last_call_id)
        assert (
            len(recorded) == 1
            and recorded[0]["origin"] == "http"
            and recorded[0]["outcome"] == "error"
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


def test_operation_access_across_transports(settings: Settings) -> None:
    @operation("accounts.manage", get="/manage")
    def manage(caller: Caller) -> None:
        return None

    app = create_app(settings)
    with pytest.raises(Forbidden):
        manage(trusted(app))
    manage(trusted(app, frozenset({"admin"})))
    with TestClient(app, headers={"Origin": "http://localhost:8000"}) as client:
        assert client.get("/api/manage").status_code == 401
        assert client.get("/health/live").status_code == 200
        assert (
            client.request("GET", "/api/manage", json={"userId": str(uuid4())}).status_code == 401
        )


def test_operation_nested_call_restores_context(app: FastAPI, last_call_id: int) -> None:
    seen = []

    @operation(SIGNED_IN)
    def inner(caller: Caller) -> None:
        seen.append(current_call.get())
        return None

    @operation(SIGNED_IN)
    def outer(caller: Caller) -> None:
        parent = current_call.get()
        inner(caller)
        assert current_call.get() is parent
        try:
            inner(replace(caller, user_id=None))
        except ApiError:
            pass
        assert current_call.get() is parent

    caller = trusted(app)
    outer(caller)
    assert current_call.get() is None
    [parent] = rows(app, "probe.outer", last_call_id)
    child = rows(app, "probe.inner", last_call_id)
    assert len(child) == 2 and all(r["parent_call_id"] == parent["call_id"] for r in child)
    assert len({r["call_id"] for r in [parent, *child]}) == 3


def test_operation_read_only_and_rollback(app: FastAPI, last_call_id: int) -> None:
    name = "test_" + uuid4().hex

    @operation(PUBLIC)
    def read(caller: Caller) -> None:
        assert caller.sessions is not None
        with unit_of_work(caller.sessions, read_only=True) as session:
            session.execute(
                text("insert into users (username,display_name) values (:name,'Test')"),
                {"name": name},
            )

    @operation(PUBLIC, effects=DATA)
    def write(caller: Caller) -> None:
        assert caller.sessions is not None
        with unit_of_work(caller.sessions) as session:
            session.execute(
                text("insert into users (username,display_name) values (:name,'Test')"),
                {"name": name},
            )
            raise RuntimeError("Synthetic failure")

    caller = trusted(app)
    for fn in [read, write]:
        with pytest.raises(ApiError):
            fn(caller)
    with app.state.engine.connect() as connection:
        assert (
            connection.execute(
                text("select count(*) from users where username=:name"), {"name": name}
            ).scalar()
            == 0
        )
    assert [row["outcome"] for row in rows(app, "probe.write", last_call_id)] == ["error"]


def test_operation_registration_rejects_invalid_signature() -> None:
    def no_caller() -> None:
        pass

    def no_output(caller: Caller):
        pass

    def probe(caller: Caller) -> None:
        pass

    for fn in [no_caller, no_output]:
        with pytest.raises(ValueError):
            operation(PUBLIC, get="/bad")(fn)
    with pytest.raises(ValueError):
        operation(PUBLIC, get="/a", post="/b")(probe)
    with pytest.raises(ValueError):
        operation(PUBLIC, tool=True)(probe)
    with pytest.raises(ValueError):
        operation(PUBLIC, get="/a", tool=True)(probe)
    operation(PUBLIC, get="/a")(probe)
    with pytest.raises(ValueError):
        operation(PUBLIC, get="/a")(probe)
    assert all(o.access for o in registry.values())


class OptionalProbe(Shape):
    value: int | None = None


def test_optional_query_shape(settings: Settings) -> None:
    @operation(PUBLIC, get="/optional")
    def optional(caller: Caller, request: OptionalProbe) -> ProbeOutput:
        return ProbeOutput(value=request.value or 0, result_value=request.value or 0)

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/optional?value=7").json()["value"] == 7
        assert client.get("/api/optional?value=nope").status_code == 400


class PositiveOutput(Shape):
    count: Annotated[int, msgspec.Meta(ge=1)]


def test_native_output_constraints_are_checked_for_shapes_and_lists(
    settings: Settings, last_call_id: int
) -> None:
    @operation(PUBLIC, get="/bad-output")
    def bad_output(caller: Caller) -> PositiveOutput:
        return PositiveOutput(count=0)

    @operation(PUBLIC, get="/bad-output-list")
    async def bad_output_list(caller: Caller) -> list[PositiveOutput]:
        return [PositiveOutput(count=0)]

    app = create_app(settings)
    with pytest.raises(ApiError):
        bad_output(trusted(app))
    with TestClient(app) as client:
        for path in ["/api/bad-output", "/api/bad-output-list"]:
            response = client.get(path)
            assert response.status_code == 500 and response.json()["code"] == "internal_error"
            cache_policy = response.headers.get("Cache-Control")
            assert cache_policy == "no-store"
    for name in ["probe.bad_output", "probe.bad_output_list"]:
        assert {row["error_code"] for row in rows(app, name, last_call_id)} == {"internal_error"}


def test_default_shape_input_on_http_and_python(app):
    from fastapi.testclient import TestClient

    from app import create_app
    from app.infrastructure.shapes import Shape

    class DefaultInput(Shape):
        value: int = 7

    @operation(PUBLIC, get="/default-input-probe")
    def default_input_probe(caller: Caller, request: DefaultInput) -> DefaultInput:
        return request

    assert default_input_probe(trusted(app)).value == 7
    with pytest.raises(InvalidInput):
        default_input_probe(trusted(app), {"value": "invalid"})
    with TestClient(create_app(app.state.settings)) as http:
        result = http.get("/api/default-input-probe")
        assert result.status_code == 200 and result.json() == {"value": 7}
