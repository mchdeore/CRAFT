"""Each call writes one safe event and append-only row without payloads."""

import io
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import create_app
from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import ApiError, InvalidInput
from app.infrastructure.logging import configure_logging
from app.infrastructure.operations import observe, operation
from app.infrastructure.operations.registry import registry
from app.infrastructure.operations.tests.test_operation import ProbeInput, rows, trusted
from app.infrastructure.shapes import Shape


class SensitiveInput(Shape):
    password: str
    token: str
    query: str


def test_every_operation_logs_one_row(app: FastAPI) -> None:
    @operation(PUBLIC)
    def success(caller: Caller, request: ProbeInput) -> None:
        return None

    @operation(PUBLIC)
    def failure(caller: Caller) -> None:
        raise RuntimeError("Synthetic")

    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    caller = trusted(app)
    before = {name: len(rows(app, name)) for name in ["probe.success", "probe.failure"]}
    success(caller, {"value": 7})
    with pytest.raises(InvalidInput):
        success(caller, {"value": "7"})
    with pytest.raises(ApiError):
        failure(caller)
    recorded = [
        *rows(app, "probe.success")[before["probe.success"] :],
        *rows(app, "probe.failure")[before["probe.failure"] :],
    ]
    output = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(recorded) == 3 and len(output) == 3
    assert {line["call_id"] for line in output} == {str(row["call_id"]) for row in recorded}


def test_operation_log_line_has_no_payload(app: FastAPI, capsys, caplog) -> None:
    markers = [
        "CRAFT_SECRET_PASSWORD_04",
        "CRAFT_SECRET_TOKEN_04",
        "CRAFT_SECRET_QUERY_04",
        "CRAFT_SECRET_ERROR_04",
    ]

    @observe("private.sensitive")
    def step(request: SensitiveInput) -> None:
        raise RuntimeError(markers[-1])

    @operation(PUBLIC, post="/sensitive")
    def sensitive(caller: Caller, request: SensitiveInput) -> None:
        step(request)

    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    with pytest.raises(ApiError):
        sensitive(
            trusted(app), SensitiveInput(password=markers[0], token=markers[1], query=markers[2])
        )
    http_app = create_app(app.state.settings)
    configure_logging("DEBUG", stream)
    logging.getLogger().addHandler(caplog.handler)
    with TestClient(http_app) as http:
        response = http.post(
            "/api/sensitive?query=" + markers[2],
            headers={"Cookie": "unused=" + markers[1]},
            json={"password": markers[0], "token": markers[1], "query": markers[2]},
        )
        assert response.status_code == 500
    captured = capsys.readouterr()
    capture_text = (
        captured.out + captured.err + "".join(record.getMessage() for record in caplog.records)
    )
    assert all(marker not in capture_text for marker in markers)
    cells = [
        str(cell)
        for row in [rows(app, "probe.sensitive")[-1], rows(app, "private.sensitive")[-1]]
        for cell in row.values()
    ]
    assert all(
        marker not in stream.getvalue() and all(marker not in cell for cell in cells)
        for marker in markers
    )
    assert not any(
        key in json.loads(stream.getvalue().splitlines()[0])
        for key in ["password", "token", "query", "body"]
    )


def test_observe_private_step_and_database_counts(app: FastAPI) -> None:
    @observe("private.query")
    def query(caller: Caller) -> None:
        assert caller.sessions is not None
        with unit_of_work(caller.sessions, read_only=True) as session:
            session.execute(text("select 1"))

    @operation(PUBLIC)
    def measured(caller: Caller) -> None:
        query(caller)

    measured(trusted(app))
    parent = rows(app, "probe.measured")[-1]
    child = rows(app, "private.query")[-1]
    assert child["kind"] == "step" and child["parent_call_id"] == parent["call_id"]
    assert child["db_statements"] >= 1 and child["db_ms"] >= 0
    assert not any(o.tool_name == "query" for o in registry.values())


def test_observe_preserves_internal_exception(app: FastAPI) -> None:
    @observe("private.exception")
    def failing() -> None:
        raise ValueError("Synthetic")

    @operation(PUBLIC)
    def handled(caller: Caller) -> None:
        with pytest.raises(ValueError):
            failing()

    handled(trusted(app))
    assert rows(app, "probe.handled")[-1]["outcome"] == "ok"
    assert rows(app, "private.exception")[-1]["outcome"] == "error"
