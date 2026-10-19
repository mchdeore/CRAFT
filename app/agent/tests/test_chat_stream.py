"""Actual framework streams, server history and durable owned run limits."""

import asyncio
import hashlib
import json
import subprocess
from dataclasses import replace
from uuid import uuid4

import msgspec
import pytest
from agent_framework import ChatResponse, Content, Message
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.agent import runtime
from app.agent.fake import FakeChatClient
from app.agent.handlers import RunAgentInput, run_agent
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import Conflict, NotFound, RateLimited
from app.infrastructure.operations.context import current_call
from app.tests.support.chat import chat as chat
from app.tests.support.cube import cube_source as cube_source


def request(chat, content="Peak power", identifier=None):
    return RunAgentInput(
        thread_id=chat["thread"],
        run_id=identifier or uuid4(),
        messages=[{"id": str(uuid4()), "role": "user", "content": content}],
    )


def collect(caller, data):
    async def execute():
        return [msgspec.to_builtins(event) async for event in run_agent(caller, data)]

    return asyncio.run(execute())


def valid_events(events):
    output = subprocess.run(
        ["node", "api/validate-stream.mjs"],
        input=json.dumps(events),
        capture_output=True,
        text=True,
    )
    return output.returncode == 0 and json.loads(output.stdout)["valid"]


def row(chat, identifier):
    with chat["owner"].connect() as connection:
        return (
            connection.execute(
                select(m.metadata.tables["agent_runs"]).where(
                    m.metadata.tables["agent_runs"].c.id == identifier
                )
            )
            .mappings()
            .one()
        )


def test_chat_stream_valid_events_in_order(chat):
    data = request(chat)
    events = collect(chat["caller"], data)
    types = [event["type"] for event in events]
    assert types[0] == "RUN_STARTED" and types[-1] == "RUN_FINISHED"
    for required in [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "ACTIVITY_SNAPSHOT",
    ]:
        assert required in types
    assert (
        types.index("TOOL_CALL_START")
        < types.index("TOOL_CALL_ARGS")
        < types.index("TOOL_CALL_END")
        < types.index("TOOL_CALL_RESULT")
        < types.index("TEXT_MESSAGE_CONTENT")
    )
    from app.tests.test_contract import validate_chat_events

    assert valid_events(events) and validate_chat_events(events)
    answer = next(event for event in events if event["type"] == "ACTIVITY_SNAPSHOT")["content"]
    supported = (
        bool(answer["citations"]) and answer["citations"][0]["handle"]["item"] == chat["item"]
    )
    assert supported and answer["confidence"]["score"] == 0
    assert all(
        hashlib.sha256(citation["quote"].encode()).hexdigest() == citation["handle"]["quoteSha256"]
        for citation in answer["citations"]
    )
    assert answer["messageId"] == next(
        event["messageId"] for event in events if event["type"] == "TEXT_MESSAGE_CONTENT"
    )
    assert row(chat, data.run_id)["status"] == "finished"
    snapshot = next(event["messages"] for event in events if event["type"] == "MESSAGES_SNAPSHOT")
    identifiers = [message["id"] for message in snapshot]
    assert len(identifiers) == len(set(identifiers))
    with chat["owner"].connect() as connection:
        stored = (
            connection.execute(
                select(m.metadata.tables["messages"].c.content).where(
                    m.metadata.tables["messages"].c.conversation_id == data.thread_id
                )
            )
            .scalars()
            .all()
        )
    assert set(identifiers) == {message["id"] for message in stored}
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as client:
        assert chat["sign_in"](client).status_code == 200
        response = client.post(
            "/api/agent/runs",
            json=msgspec.to_builtins(request(chat)),
            headers={"Origin": "https://testserver"},
        )
        streamed = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert response.status_code == 200 and valid_events(streamed)
        assert client.get("/api/tools").status_code == 200


def test_encrypted_reasoning_stream_keeps_opaque_value_and_message_reference(chat, monkeypatch):
    class ReasoningClient(FakeChatClient):
        def reply(self, messages):
            return ChatResponse(
                messages=[
                    Message(
                        role="assistant",
                        contents=[
                            Content.from_text_reasoning(
                                id="generated-reasoning", protected_data="opaque-generated-fixture"
                            ),
                            Content.from_text("Generated answer"),
                        ],
                        message_id="generated-response",
                    )
                ],
                model="fake",
            )

    monkeypatch.setattr(runtime.model, "client", lambda _: ReasoningClient())
    data = request(chat, "Give a generated answer")
    events = collect(chat["caller"], data)
    assert events[-1]["type"] == "RUN_FINISHED"
    encrypted = next(event for event in events if event["type"] == "REASONING_ENCRYPTED_VALUE")
    reasoning = next(event for event in events if event["type"] == "REASONING_MESSAGE_START")
    assert encrypted["subtype"] == "message"
    assert encrypted["entityId"] == reasoning["messageId"]
    assert encrypted["encryptedValue"] == "opaque-generated-fixture"
    assert all("opaque-generated-fixture" not in event.get("delta", "") for event in events)
    assert valid_events(events) and row(chat, data.run_id)["status"] == "finished"


def test_browser_cannot_inject_history(chat, monkeypatch):
    seen = []
    original = FakeChatClient.reply

    def inspect(self, messages):
        seen.append([message.text for message in messages])
        return original(self, messages)

    monkeypatch.setattr(FakeChatClient, "reply", inspect)
    forged = uuid4().hex
    data = msgspec.to_builtins(request(chat)) | {
        "tools": [{"name": forged}],
        "state": {"instructions": forged},
        "context": [{"value": forged}],
        "instructions": forged,
        "identity": forged,
    }
    data["messages"].insert(0, {"id": str(uuid4()), "role": "assistant", "content": forged})
    events = collect(chat["caller"], data)
    safe = all(forged not in text for texts in seen for text in texts)
    assert safe and events[-1]["type"] == "RUN_FINISHED"


def test_chat_stream_owner_and_run_limits(chat):
    data = request(chat)
    collect(chat["caller"], data)
    with pytest.raises(Conflict):
        collect(chat["caller"], data)
    with pytest.raises(NotFound):
        collect(replace(chat["caller"], user_id=uuid4()), request(chat))
    table = m.metadata.tables["agent_runs"]
    with chat["owner"].begin() as connection:
        connection.execute(
            table.insert().values(
                id=uuid4(),
                conversation_id=chat["thread"],
                started_by=chat["caller"].user_id,
                status="running",
                model="fake",
                instructions_version="simulated",
            )
        )
    with pytest.raises(Conflict):
        collect(chat["caller"], request(chat))
    with chat["owner"].begin() as connection:
        connection.execute(
            table.update()
            .where(table.c.conversation_id == chat["thread"])
            .values(status="finished")
        )
        for _ in range(3):
            connection.execute(
                table.insert().values(
                    id=uuid4(),
                    conversation_id=chat["thread"],
                    started_by=chat["caller"].user_id,
                    status="running",
                    model="fake",
                    instructions_version="simulated",
                )
            )
    # Other conversations for the same account are counted even when this one is free.
    with chat["owner"].begin() as connection:
        second = connection.execute(
            m.metadata.tables["conversations"]
            .insert()
            .values(owner_id=chat["caller"].user_id, title="Simulated second")
            .returning(m.metadata.tables["conversations"].c.id)
        ).scalar_one()
    try:
        with pytest.raises(RateLimited):
            collect(chat["caller"], msgspec.structs.replace(request(chat), thread_id=second))
    finally:
        with chat["owner"].begin() as connection:
            connection.execute(
                m.metadata.tables["conversations"]
                .delete()
                .where(m.metadata.tables["conversations"].c.id == second)
            )


def test_chat_stream_closed_stream_cancels_run(chat, monkeypatch):
    async def execute():
        entered = asyncio.Event()
        original = FakeChatClient._inner_get_response

        def blocked(self, **kwargs):
            from agent_framework import ResponseStream

            async def updates():
                entered.set()
                await asyncio.Event().wait()
                if False:
                    yield None

            return ResponseStream(updates(), finalizer=lambda _: None)

        monkeypatch.setattr(FakeChatClient, "_inner_get_response", blocked)
        data = request(chat)
        iterator = run_agent(chat["caller"], data)
        assert (await anext(iterator)).type == "RUN_STARTED"
        task = asyncio.create_task(anext(iterator))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await iterator.aclose()
        assert row(chat, data.run_id)["status"] == "cancelled"
        cleared = row(chat, data.run_id)["token_sha256"] is None
        assert cleared
        assert current_call.get() is None
        monkeypatch.setattr(FakeChatClient, "_inner_get_response", original)

    asyncio.run(execute())


def test_chat_stream_no_transaction_or_payload_logs(chat, monkeypatch, capsys):
    original = FakeChatClient.reply
    checks = []
    marker = uuid4().hex

    def inspected(self, messages):
        checks.append(chat["app"].state.engine.pool.checkedout() == 0)
        return original(self, messages)

    monkeypatch.setattr(FakeChatClient, "reply", inspected)
    collect(chat["caller"], request(chat, marker))
    captured = capsys.readouterr()
    safe = marker not in captured.out + captured.err
    assert checks and all(checks) and safe


def test_chat_stream_tool_and_time_budgets(chat, monkeypatch):
    monkeypatch.setattr(runtime, "TOOL_LIMIT", 0)
    data = request(chat)
    events = collect(chat["caller"], data)
    assert events[-1]["type"] == "RUN_ERROR" and row(chat, data.run_id)["status"] == "failed"
    cleared = row(chat, data.run_id)["token_sha256"] is None
    assert cleared

    monkeypatch.setattr(runtime, "TOOL_LIMIT", 20)
    monkeypatch.setattr(runtime, "RUN_SECONDS", 0.000001)
    data = request(chat)
    events = collect(chat["caller"], data)
    assert events[-1]["type"] == "RUN_ERROR" and row(chat, data.run_id)["status"] == "failed"
    cleared = row(chat, data.run_id)["token_sha256"] is None
    assert cleared


def test_chat_stream_output_budget(chat, monkeypatch):
    from agent_framework import ChatResponse, Message

    configured = msgspec.structs.replace(chat["settings"], model_max_output_tokens=16)
    caller = replace(chat["caller"], settings=configured)
    monkeypatch.setattr(
        FakeChatClient,
        "reply",
        lambda self, messages: ChatResponse(
            messages=[Message(role="assistant", contents=["Simulated " * 30])]
        ),
    )
    data = request(chat)
    events = collect(caller, data)
    assert events[-1]["type"] == "RUN_ERROR" and row(chat, data.run_id)["status"] == "failed"
    cleared = row(chat, data.run_id)["token_sha256"] is None
    assert cleared
    with chat["owner"].connect() as connection:
        result = (
            connection.execute(
                select(m.metadata.tables["call_log"].c.outcome).where(
                    m.metadata.tables["call_log"].c.run_id == data.run_id,
                    m.metadata.tables["call_log"].c.name == "agent.run_agent",
                )
            )
            .scalars()
            .one()
        )
    assert result == "error"


def test_http_disconnect_preserves_state_and_cancels(chat):
    data = request(chat)
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as client:
        assert chat["sign_in"](client).status_code == 200
        cookie = client.cookies.get("__Host-craft_session")
    assert cookie is not None

    async def execute():
        started = asyncio.Event()
        body_read = False

        async def receive():
            nonlocal body_read
            if not body_read:
                body_read = True
                return {
                    "type": "http.request",
                    "body": msgspec.json.encode(data),
                    "more_body": False,
                }
            await started.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                started.set()
                await asyncio.Event().wait()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/agent/runs",
            "raw_path": b"/api/agent/runs",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"origin", b"https://testserver"),
                (b"content-type", b"application/json"),
                (b"cookie", ("__Host-craft_session=" + cookie).encode()),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
        await asyncio.wait_for(chat["app"](scope, receive, send), 5)
        assert started.is_set() and row(chat, data.run_id)["status"] == "cancelled"
        assert chat["app"].state.engine.pool.checkedout() == 0
        with chat["owner"].connect() as connection:
            stored = connection.execute(
                select(m.metadata.tables["agent_sessions"].c.state).where(
                    m.metadata.tables["agent_sessions"].c.conversation_id == data.thread_id
                )
            ).scalar_one()
        assert bool(stored)

    asyncio.run(execute())
