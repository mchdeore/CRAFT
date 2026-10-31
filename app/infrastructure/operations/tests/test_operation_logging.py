"""Each call writes one safe event and append-only row without payloads."""

import asyncio
import io
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app import create_app
from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import ApiError, InvalidInput
from app.infrastructure.logging import configure_logging
from app.infrastructure.operations import observe, operation, storage
from app.infrastructure.operations.jobs import SCHEDULED, runner
from app.infrastructure.operations.registry import registry
from app.infrastructure.queue import PermanentError
from app.infrastructure.shapes import Shape
from app.tests.support import jobs as test_jobs
from app.tests.support.chat import chat as chat
from app.tests.support.cube import cube_source as cube_source
from app.tests.support.operations import ProbeInput, rows, trusted


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
    with TestClient(http_app, headers={"Origin": "http://localhost:8000"}) as http:
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


def test_async_cancellation_records_failure(app: FastAPI) -> None:
    @observe("private.cancelled_step")
    async def step():
        await asyncio.Event().wait()

    @operation(PUBLIC)
    async def cancelled_operation(caller: Caller) -> None:
        await step()

    async def execute():
        task = asyncio.create_task(cancelled_operation(trusted(app)))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(execute())
    parent = rows(app, "probe.cancelled_operation")[-1]
    child = rows(app, "private.cancelled_step")[-1]
    assert parent["outcome"] == child["outcome"] == "error"
    assert child["parent_call_id"] == parent["call_id"]


def test_a_call_log_outage_changes_no_result(settings, monkeypatch) -> None:
    """With call_log unwritable, a call still answers and a job keeps its own error; the log
    names the failure's type and nothing else."""

    @operation(PUBLIC, get="/call-log-outage")
    def answered(caller: Caller) -> ProbeInput:
        return ProbeInput(value=7)

    def unwritable(session, fields):
        raise OperationalError("insert into call_log", {}, RuntimeError("generated outage"))

    monkeypatch.setattr(storage, "append_call", unwritable)
    app = create_app(settings)
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    job = SimpleNamespace(sessions=app.state.sessions, settings=settings, id=1, attempt=1)
    requester = SCHEDULED["caller"] | {"user": str(uuid4()), "roles": ["member"]}
    try:
        with TestClient(app) as client:
            response = client.get("/api/call-log-outage")
        run = runner(test_jobs.sleep.__operation__)
        with pytest.raises(PermanentError):
            asyncio.run(run(job, {"fail": "permanent"}, requester, None))
    finally:
        app.state.engine.dispose()
    assert response.status_code == 200 and response.json() == {"value": 7}
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    lost = [line for line in lines if line["event"] == "call_log_record_failed"]
    assert [line["error_type"] for line in lost] == ["OperationalError"] * 2
    assert "generated outage" not in stream.getvalue()


def test_malformed_protocol_logging_has_no_input() -> None:
    import anyio
    from mcp.server.models import InitializationOptions
    from mcp.server.session import ServerSession
    from mcp.shared.message import SessionMessage
    from mcp.types import JSONRPCMessage, JSONRPCRequest, ServerCapabilities

    async def execute():
        marker = uuid4().hex
        stream = io.StringIO()
        configure_logging("DEBUG", stream)
        incoming, read = anyio.create_memory_object_stream(0)
        write, outgoing = anyio.create_memory_object_stream(0)
        options = InitializationOptions(
            server_name="craft", server_version="1", capabilities=ServerCapabilities()
        )
        async with ServerSession(read, write, options, stateless=True):
            await incoming.send(
                SessionMessage(
                    JSONRPCMessage(
                        JSONRPCRequest(
                            jsonrpc="2.0",
                            id=1,
                            method="tools/call",
                            params={"name": {"value": marker}, "arguments": {}},
                        )
                    )
                )
            )
            response = await outgoing.receive()
        refused = hasattr(response.message.root, "error")
        safe = marker not in stream.getvalue()
        assert refused and safe

    anyio.run(execute)


def test_native_mcp_logs_have_no_payload(chat, capsys, caplog) -> None:
    from dataclasses import replace

    from sqlalchemy import select

    from app.agent import service
    from app.agent.handlers import RunRef, issue_run_token, revoke_run_token
    from app.infrastructure.database import metadata as m
    from app.tests.support.chat import request
    from app.tests.support.mcp import session_stream

    data = request(chat)
    service.start(chat["caller"], data, "simulated")
    caller = replace(chat["caller"], conversation_id=data.thread_id, run_id=data.run_id)
    raw = issue_run_token(caller, RunRef(run=data.run_id)).token
    marker = uuid4().hex
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    logging.getLogger().addHandler(caplog.handler)

    async def execute():
        async with session_stream(chat["settings"].mcp_url, raw) as session:
            result = await session.call_tool("search", {"query": marker})
            assert not result.isError

    try:
        asyncio.run(execute())
        captured = capsys.readouterr()
        logs = m.metadata.tables["call_log"]
        with chat["owner"].connect() as connection:
            recorded = (
                connection.execute(select(logs).where(logs.c.run_id == data.run_id))
                .mappings()
                .all()
            )
        text = stream.getvalue() + captured.out + captured.err + caplog.text
        safe = all(
            value not in text
            and all(value not in str(cell) for record in recorded for cell in record.values())
            for value in [raw, marker]
        )
        assert safe
        calls = [record for record in recorded if record["name"] == "library.search"]
        assert len(calls) == 1 and calls[0]["origin"] == "mcp" and calls[0]["outcome"] == "ok"
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert all(
            not {"password", "token", "query", "body", "arguments"}.intersection(event)
            for event in events
        )
    finally:
        revoke_run_token(caller, RunRef(run=data.run_id))
        service.finish(caller, data, "cancelled", {})
