"""Offline framework clients, actual intercepted HTTP requests and real transaction boundaries."""

import asyncio
import hashlib
import io
import json
from typing import Any
from uuid import uuid4

import httpx2 as httpx
import msgspec
import pytest
from agent_framework import Message
from sqlalchemy import select, text

from app.agent import model
from app.agent.handlers import EmbeddingInput, embed
from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import ApiError
from app.infrastructure.logging import configure_logging
from app.infrastructure.operations import operation
from app.infrastructure.operations.registry import registry
from app.infrastructure.operations.storage import call_log
from app.tests.support import model as scripted_model


@pytest.fixture(autouse=True)
def restore_registry():
    previous = registry.copy()
    yield
    registry.clear()
    registry.update(previous)


class Intercept:
    def __init__(self, settings):
        self.secrets = {name: uuid4().hex for name in ["key", "prompt", "answer", "endpoint"]}
        self.settings = msgspec.structs.replace(
            settings,
            model_main="gpt-5.2",
            model_check="check-simulated",
            model_embedding="text-embedding-3-large",
            model_endpoint="https://model.synthetic.invalid/api/projects/craft",
            model_key=self.secrets["key"],
            model_max_output_tokens=200,
        )
        self.received = []
        self.examiner = lambda: True
        self.boundaries = []
        self.fail = False

    def __repr__(self):
        return "Intercepted model transport"

    def messages(self):
        return [Message(role="user", contents=[self.secrets["prompt"]])]

    def no_leaks(self, output):
        return all(marker not in output for marker in self.secrets.values())

    async def receive(self, request):
        body = json.loads(request.content)
        self.boundaries.append(self.examiner())
        self.received.append(
            {
                "url": str(request.url),
                "model": body.get("model"),
                "store": body.get("store"),
                "budget": body.get("max_output_tokens"),
                "effort": body.get("reasoning", {}).get("effort"),
                "dimensions": body.get("dimensions"),
            }
        )
        if self.fail:
            return httpx.Response(
                500,
                json={
                    "error": {
                        "message": self.secrets["answer"],
                        "type": "synthetic_failure",
                        "code": "synthetic_failure",
                    }
                },
            )
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "model": body["model"],
                    "data": [
                        {"object": "embedding", "embedding": [1.0] + [0.0] * 1535, "index": index}
                        for index, _ in enumerate(
                            body["input"] if isinstance(body["input"], list) else [body["input"]]
                        )
                    ],
                    "usage": {"prompt_tokens": 4, "total_tokens": 4},
                },
            )
        response = {
            "id": "resp_simulated",
            "object": "response",
            "created_at": 1780000000,
            "status": "completed",
            "model": body["model"],
            "output": [
                {
                    "type": "message",
                    "id": "msg_simulated",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": self.secrets["answer"],
                            "annotations": [],
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 4, "output_tokens": 4, "total_tokens": 8},
            "parallel_tool_calls": False,
            "error": None,
            "incomplete_details": None,
            "instructions": None,
            "metadata": {},
            "temperature": 1,
            "tool_choice": "auto",
            "tools": [],
            "top_p": 1,
            "max_output_tokens": body["max_output_tokens"],
        }
        if body.get("stream"):
            events = [
                {
                    "type": "response.created",
                    "response": response | {"output": [], "status": "in_progress"},
                },
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": response["output"][0] | {"content": [], "status": "in_progress"},
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_simulated",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": self.secrets["answer"],
                },
                {
                    "type": "response.output_text.done",
                    "item_id": "msg_simulated",
                    "output_index": 0,
                    "content_index": 0,
                    "text": self.secrets["answer"],
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": response["output"][0],
                },
                {"type": "response.completed", "response": response},
            ]

            class SourceStream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    for event in events:
                        await asyncio.sleep(0)
                        yield (
                            "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
                        ).encode()

            return httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, stream=SourceStream()
            )
        return httpx.Response(200, json=response)

    def transport(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.receive))


@pytest.fixture
def intercepted(settings, monkeypatch):
    intercepted = Intercept(settings)
    monkeypatch.setattr(model, "transport", intercepted.transport)
    return intercepted


def caller(app, settings=None):
    return Caller(sessions=app.state.sessions, settings=settings or app.state.settings)


def log_rows(app, names):
    with app.state.engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(call_log).where(call_log.c.name.in_(names)).order_by(call_log.c.id)
            ).mappings()
        ]


def test_scripted_model_deterministic(settings):
    scripted = model.client(settings)
    messages = [Message(role="user", contents=["Synthetic thermal evidence"])]

    async def run():
        first = await scripted.get_response(messages)
        second = await scripted.get_response(messages)
        stream = scripted.get_response(messages, stream=True)
        updates = [update async for update in stream]
        final = await stream.get_final_response()

        def meaning(response):
            plans = []
            for message in response.messages:
                for content in message.contents:
                    if content.type == "function_call":
                        arguments = content.arguments
                        if isinstance(arguments, str):
                            arguments = json.loads(arguments)
                        plans.append((content.name, arguments))
            return response.text, plans

        identical = meaning(first) == meaning(second) == meaning(final)
        assert identical and updates
        assert first.messages[0].contents[0].name == "search"

    asyncio.run(run())
    first = scripted_model.embedding("Synthetic embedding")
    stable = first == scripted_model.embedding("Synthetic embedding")
    assert stable and len(first) == 1536


def test_model_adapter_configured_endpoint_and_store_false(app, intercepted):
    @operation(PUBLIC)
    async def probe(caller: Caller) -> None:
        roles: list[model.Role] = ["main", "check"]
        for role in roles:
            selected = model.client(intercepted.settings, role)
            try:
                unsafe_options: Any = {
                    "store": True,
                    "max_tokens": 999,
                    "max_output_tokens": 999,
                    "model": "wrong-simulated",
                    "extra_body": {"store": True, "max_output_tokens": 999},
                }
                result = await selected.get_response(
                    intercepted.messages(),
                    options=unsafe_options,
                )
                correct = result.text == intercepted.secrets["answer"]
                assert correct
            finally:
                await selected.close()

    asyncio.run(probe(caller(app, intercepted.settings)))
    received = intercepted.received
    exact = len(received) == 2 and all(
        r["url"].startswith(intercepted.settings.model_endpoint + "/openai/v1/responses")
        and r["store"] is False
        and r["budget"] == 200
        for r in received
    )
    assert exact
    assert [(r["model"], r["effort"]) for r in received] == [
        ("gpt-5.2", "medium"),
        ("check-simulated", "low"),
    ]


def test_model_adapter_no_transaction_open_during_call(app, intercepted):
    @operation(PUBLIC)
    async def transaction_probe(caller: Caller) -> None:
        assert caller.sessions is not None
        with unit_of_work(caller.sessions, read_only=True) as session:
            session.execute(text("select 1"))
        intercepted.examiner = lambda: not session.in_transaction()
        selected = model.client(intercepted.settings)
        try:
            await selected.get_response(intercepted.messages())
        finally:
            await selected.close()

    asyncio.run(transaction_probe(caller(app, intercepted.settings)))
    assert intercepted.boundaries and all(intercepted.boundaries)
    recorded = log_rows(app, ["agent.model", transaction_probe.__operation__.name])
    operation_rows = [row for row in recorded if row["kind"] == "operation"]
    assert operation_rows
    assert any(
        row["parent_call_id"] == operation_rows[-1]["call_id"]
        for row in recorded
        if row["kind"] == "step"
    )


def test_model_adapter_no_prompt_answer_or_key_logs(app, intercepted, capsys):
    output = io.StringIO()
    configure_logging("DEBUG", output)

    @operation(PUBLIC)
    async def log_probe(caller: Caller) -> None:
        selected = model.client(intercepted.settings)
        try:
            await selected.get_response(intercepted.messages())
        finally:
            await selected.close()

    trusted = caller(app, intercepted.settings)
    asyncio.run(log_probe(trusted))
    intercepted.fail = True
    with pytest.raises(ApiError):
        asyncio.run(log_probe(trusted))
    bad = msgspec.structs.replace(
        intercepted.settings,
        model_endpoint=intercepted.settings.model_endpoint
        + "?private="
        + intercepted.secrets["endpoint"],
    )
    with pytest.raises(SystemExit):
        model.client(bad)
    safe = intercepted.no_leaks(output.getvalue() + capsys.readouterr().out)
    assert safe
    records = log_rows(app, ["agent.model"])
    safe = intercepted.no_leaks(str(records))
    assert safe and any(row["outcome"] == "error" for row in records)


def test_model_adapter_settings_fail_safe(settings, intercepted, monkeypatch):
    raw = {
        "CRAFT_APP_DB_PASSWORD": settings.app_db_password,
        "CRAFT_MODEL_MAIN": "gpt-5.2",
        "CRAFT_MODEL_ENDPOINT": intercepted.settings.model_endpoint,
        "CRAFT_MODEL_KEY": intercepted.secrets["key"],
    }
    for field, value in [
        ("MODEL_KEY", ""),
        ("MODEL_ENDPOINT", "not-an-endpoint"),
        ("MODEL_MAX_OUTPUT_TOKENS", "0"),
        ("MODEL_MAX_OUTPUT_TOKENS", "16001"),
        ("MODEL_CHECK", ""),
        ("MODEL_EMBEDDING", ""),
    ]:
        with pytest.raises(SystemExit) as failure:
            Settings.from_env(raw | {"CRAFT_" + field: value})
        safe = intercepted.no_leaks(str(failure.value)) and field.lower() in str(failure.value)
        assert safe
    monkeypatch.setenv("AZURE_AI_PROJECTS_CONSOLE_LOGGING", "true")
    with pytest.raises(SystemExit) as failure:
        model.client(intercepted.settings)
    assert "model_logging" in str(failure.value)
    unconfigured = Settings.from_env({"CRAFT_APP_DB_PASSWORD": settings.app_db_password})
    assert unconfigured.model_main == "gpt-5.2" and not unconfigured.model_key
    with pytest.raises(ApiError):
        model.client(unconfigured)


def test_embedding(app, intercepted):
    result = embed(
        caller(app, intercepted.settings), EmbeddingInput(text="Synthetic embedding input")
    )
    assert len(result.vector) == 1536 and result.model == intercepted.settings.model_embedding
    assert intercepted.received[-1]["dimensions"] == 1536
    scripted = embed(caller(app), EmbeddingInput(text="Synthetic embedding input"))
    assert scripted.model == app.state.settings.model_embedding


def test_scripted_framework_tool_invocation(settings):
    from agent_framework import tool

    calls = []

    @tool
    def search(query: str) -> dict:
        calls.append(True)
        source = "Simulated evidence only."
        return {
            "hits": [
                {
                    "text": source,
                    "handle": {
                        "simulated": True,
                        "quoteSha256": hashlib.sha256(source.encode()).hexdigest(),
                    },
                }
            ]
        }

    selected = model.client(settings)

    async def run():
        result = await selected.get_response(
            [Message(role="user", contents=["Synthetic evidence"])], options={"tools": [search]}
        )
        grounded = result.text == "Simulated evidence only."
        assert grounded and len(calls) == 1

    asyncio.run(run())


def test_model_stream_observation(app, intercepted):
    @operation(PUBLIC)
    async def stream_probe(caller: Caller) -> None:
        selected = model.client(intercepted.settings)
        try:
            stream = selected.get_response(intercepted.messages(), stream=True)
            output = []
            async for update in stream:
                output.extend(content.text for content in update.contents if content.type == "text")
            final = await stream.get_final_response()
            correct = "".join(output) == final.text == intercepted.secrets["answer"]
            assert correct
        finally:
            await selected.close()

    asyncio.run(stream_probe(caller(app, intercepted.settings)))
    records = log_rows(app, ["agent.model", stream_probe.__operation__.name])
    root = [row for row in records if row["kind"] == "operation"][-1]
    assert any(
        row["kind"] == "step" and row["parent_call_id"] == root["call_id"] for row in records
    )


def test_scripted_summary_task(settings):
    selected = model.client(settings)
    data = {
        "task": "summarize",
        "blocks": [
            {"block": 1, "text": "Simulated fact one."},
            {"block": 2, "text": "| Stage | C |", "headingPath": "Stage B"},
        ],
    }

    async def run():
        result = await selected.get_response([Message(role="user", contents=[json.dumps(data)])])
        body = json.loads(result.text)
        exact = [sentence["blocks"] for sentence in body["sentences"]] == [[1], [2]]
        assert exact and len(body["sentences"]) == 2
        stages = {row["value"] for row in body["filings"] if row["field"] == "stage"}
        assert stages == {"B", "C"}

    asyncio.run(run())
