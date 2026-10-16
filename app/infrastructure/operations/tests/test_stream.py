"""Declared streams retain checks, one call record and context through consumption."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, ClassVar

import msgspec
import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.errors import ApiError, InvalidInput
from app.infrastructure.operations import operation
from app.infrastructure.operations.context import current_call
from app.infrastructure.operations.tests.test_operation import rows, trusted
from app.infrastructure.shapes import Shape


class StreamInput(Shape):
    max_bytes: ClassVar[int] = 1024
    value: Annotated[int, msgspec.Meta(ge=1, le=9)]


class StreamEvent(Shape):
    value: Annotated[int, msgspec.Meta(ge=1, le=9)]


def test_stream_python_and_http(app):
    @operation(PUBLIC, post="/stream-probe")
    async def stream_events(caller: Caller, request: StreamInput) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(value=request.value)
        yield StreamEvent(value=request.value + 1)

    async def execute():
        result = [event async for event in stream_events(trusted(app), {"value": 2})]
        assert [event.value for event in result] == [2, 3]
        assert current_call.get() is None
        with pytest.raises(InvalidInput):
            await anext(stream_events(trusted(app), {"value": "2"}))

    asyncio.run(execute())
    assert [row["outcome"] for row in rows(app, "probe.stream_events")][-2:] == ["ok", "error"]
    with TestClient(create_app(app.state.settings)) as client:
        headers = {"Origin": "http://localhost:8000"}
        response = client.post("/api/stream-probe", json={"value": 2}, headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert events == [{"value": 2}, {"value": 3}]
        assert client.post("/api/stream-probe", json={"value": 2}).status_code == 403
        too_large = client.post("/api/stream-probe", content=b" " * 1025, headers=headers)
        assert too_large.status_code == 413


def test_stream_close_runs_finalizer_and_records_failure(app):
    closed = []

    @operation(PUBLIC)
    async def stream_close(caller: Caller) -> AsyncIterator[StreamEvent]:
        try:
            yield StreamEvent(value=1)
            yield StreamEvent(value=2)
        finally:
            closed.append(True)

    async def execute():
        iterator = stream_close(trusted(app))
        assert (await anext(iterator)).value == 1
        assert current_call.get() is None
        await iterator.aclose()
        assert current_call.get() is None

    asyncio.run(execute())
    assert closed == [True]
    assert rows(app, "probe.stream_close")[-1]["outcome"] == "error"


def test_stream_native_output_is_checked(app):
    @operation(PUBLIC)
    async def stream_invalid(caller: Caller) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(value=0)

    async def execute():
        with pytest.raises(ApiError):
            await anext(stream_invalid(trusted(app)))

    asyncio.run(execute())
    assert rows(app, "probe.stream_invalid")[-1]["outcome"] == "error"


def test_stream_cannot_be_a_tool():
    with pytest.raises(ValueError):

        @operation(PUBLIC, post="/stream-tool", tool=True)
        async def stream_tool(caller: Caller) -> AsyncIterator[StreamEvent]:
            """Stream a simulated value; for example 1."""
            yield StreamEvent(value=1)
