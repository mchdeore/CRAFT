"""Actual framework streams, server history and durable owned run limits."""

import asyncio
import hashlib
import json
import subprocess
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import msgspec
import pytest
from agent_framework import ChatResponse, Content, Message
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.agent import runtime
from app.agent.handlers import (
    RunAgentInput,
    RunEventsRef,
    RunRef,
    cancel_run,
    run_agent,
    run_events,
)
from app.agent.usage import model_usage
from app.infrastructure import queue
from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_engine
from app.infrastructure.errors import Conflict, NotFound, RateLimited
from app.tests.support.chat import chat as chat
from app.tests.support.cube import cube_source as cube_source
from app.tests.support.model import ScriptedChatClient


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
        ["node", "app/contract/validate-stream.mjs"],
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


@pytest.mark.slow
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
    class ReasoningClient(ScriptedChatClient):
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
                model="scripted",
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
    original = ScriptedChatClient.reply

    def inspect(self, messages):
        seen.append([message.text for message in messages])
        return original(self, messages)

    monkeypatch.setattr(ScriptedChatClient, "reply", inspect)
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
                model="scripted",
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
                    model="scripted",
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


def wait_for(check, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def test_closed_stream_keeps_the_run_and_cancel_stops_it(chat, monkeypatch):
    entered = threading.Event()

    def blocked(self, **kwargs):
        from agent_framework import ResponseStream

        async def updates():
            entered.set()
            await asyncio.Event().wait()
            if False:
                yield None

        return ResponseStream(updates(), finalizer=lambda _: None)

    monkeypatch.setattr(ScriptedChatClient, "_inner_get_response", blocked)
    data = request(chat)

    async def leave():
        iterator = run_agent(chat["caller"], data)
        assert (await anext(iterator)).type == "RUN_STARTED"
        await iterator.aclose()

    asyncio.run(leave())
    assert entered.wait(10) and row(chat, data.run_id)["status"] == "running"
    cancel_run(chat["caller"], RunRef(run=data.run_id))
    wait_for(lambda: row(chat, data.run_id)["status"] == "cancelled")
    assert row(chat, data.run_id)["token_sha256"] is None

    async def reattach():
        return [
            msgspec.to_builtins(event)
            async for event in run_events(chat["caller"], RunEventsRef(run=data.run_id, after=1))
        ]

    events = asyncio.run(reattach())
    assert events[-1] == {
        "type": "RUN_ERROR",
        "code": "run_cancelled",
        "message": "Run stopped.",
    }
    assert all(event["type"] != "RUN_STARTED" for event in events)


def test_chat_stream_no_transaction_or_payload_logs(chat, monkeypatch, capsys):
    original = ScriptedChatClient.reply
    checks = []
    marker = uuid4().hex
    engines = []

    def engine(settings):
        engines.append(make_engine(settings))
        return engines[-1]

    def inspected(self, messages):
        # No transaction stays open while the model answers; the previous event's short
        # write may still be finishing on another thread.
        deadline = time.monotonic() + 1
        while engines[-1].pool.checkedout() and time.monotonic() < deadline:
            time.sleep(0.01)
        checks.append(engines[-1].pool.checkedout() == 0)
        return original(self, messages)

    monkeypatch.setattr(queue, "make_engine", engine)
    monkeypatch.setattr(ScriptedChatClient, "reply", inspected)
    with chat["worker"].using(chat["settings"]):
        collect(chat["caller"], request(chat, marker))
    captured = capsys.readouterr()
    safe = marker not in captured.out + captured.err
    assert checks and all(checks) and safe


def test_chat_stream_tool_and_time_budgets(chat, monkeypatch):
    monkeypatch.setattr(runtime, "TOOL_LIMIT", 0)
    data = request(chat)
    events = collect(chat["caller"], data)
    recorded = row(chat, data.run_id)
    assert events[-1]["type"] == "RUN_ERROR" and recorded["status"] == "failed"
    assert recorded["end_reason"] == "tool_limit"
    assert recorded["tool_calls"] is not None and recorded["active_seconds"] is not None
    assert recorded["token_sha256"] is None

    monkeypatch.setattr(runtime, "TOOL_LIMIT", 20)
    monkeypatch.setattr(runtime, "RUN_SECONDS", 0.000001)
    data = request(chat)
    events = collect(chat["caller"], data)
    recorded = row(chat, data.run_id)
    assert events[-1]["type"] == "RUN_ERROR" and recorded["status"] == "failed"
    assert recorded["end_reason"] == "time_limit"
    assert recorded["token_sha256"] is None


def test_task_mode_runs_longer_once_per_person_and_notifies(chat, monkeypatch):
    notifications = m.metadata.tables["notifications"]

    def notified():
        with chat["owner"].connect() as connection:
            return set(
                connection.execute(
                    select(notifications.c.kind, notifications.c.ref_id).where(
                        notifications.c.user_id == chat["caller"].user_id
                    )
                ).tuples()
            )

    # Past the chat limit, a task-mode run still has its own 60 tool calls.
    monkeypatch.setattr(runtime, "TOOL_LIMIT", 0)
    task = msgspec.structs.replace(request(chat), mode="task")
    events = collect(chat["caller"], task)
    recorded = row(chat, task.run_id)
    assert events[-1]["type"] == "RUN_FINISHED"
    assert (recorded["status"], recorded["mode"]) == ("finished", "task")
    plain = request(chat)
    collect(chat["caller"], plain)
    assert row(chat, plain.run_id)["end_reason"] == "tool_limit"
    assert notified() == {("run_done", str(task.run_id))}

    # A second task-mode run waits until the person's running one ends.
    runs, conversations = m.metadata.tables["agent_runs"], m.metadata.tables["conversations"]
    with chat["owner"].begin() as connection:
        other = connection.execute(
            conversations.insert()
            .values(owner_id=chat["caller"].user_id, title="Simulated task")
            .returning(conversations.c.id)
        ).scalar_one()
        running = uuid4()
        connection.execute(
            runs.insert().values(
                id=running,
                conversation_id=other,
                started_by=chat["caller"].user_id,
                status="running",
                model="scripted",
                instructions_version="simulated",
                mode="task",
            )
        )
        connection.execute(
            m.metadata.tables["agent_run_inputs"]
            .insert()
            .values(run_id=running, segment=1, owner_id=chat["caller"].user_id, input={})
        )
    try:
        with pytest.raises(RateLimited):
            collect(chat["caller"], msgspec.structs.replace(request(chat), mode="task"))
        collect(chat["caller"], request(chat))
    finally:
        with chat["owner"].begin() as connection:
            connection.execute(runs.delete().where(runs.c.conversation_id == other))
            connection.execute(conversations.delete().where(conversations.c.id == other))


def test_chat_stream_output_budget(chat, monkeypatch):
    from agent_framework import ChatResponse, Message

    configured = msgspec.structs.replace(chat["settings"], model_max_output_tokens=16)
    caller = replace(chat["caller"], settings=configured)
    monkeypatch.setattr(
        ScriptedChatClient,
        "reply",
        lambda self, messages: ChatResponse(
            messages=[Message(role="assistant", contents=["Simulated " * 30])]
        ),
    )
    data = request(chat)
    with chat["worker"].using(configured):
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


def test_chat_stream_spend_cap(chat, monkeypatch):
    # Each scripted response reports 10 input tokens (4 cached) and 10 output tokens, $0.25 at
    # these prices: one run (a search, then the answer) spends the budget and the next is refused.
    monkeypatch.setattr(
        ScriptedChatClient,
        "usage",
        {"input_token_count": 10, "cache_read_input_token_count": 4, "output_token_count": 10},
    )
    since = datetime.now(UTC)
    configured = msgspec.structs.replace(
        chat["settings"],
        model_budget_since=since,
        model_budget_usd=0.5,
        model_price_input=12500.0,
        model_price_output=12500.0,
    )
    caller = replace(chat["caller"], settings=configured)
    refused = request(chat)
    with chat["worker"].using(configured):
        finished = collect(caller, request(chat))
        stopped = collect(caller, refused)
    assert finished[-1]["type"] == "RUN_FINISHED"
    assert stopped[-1] == {
        "type": "RUN_ERROR",
        "code": "budget_exhausted",
        "message": "Model budget reached.",
    }
    assert row(chat, refused.run_id)["status"] == "failed"
    with chat["owner"].connect() as connection:
        rows = connection.execute(
            select(
                model_usage.c.role,
                model_usage.c.input_tokens,
                model_usage.c.cached_input_tokens,
                model_usage.c.output_tokens,
            ).where(model_usage.c.at >= since)
        ).all()
    assert [tuple(usage) for usage in rows] == [("main", 10, 4, 10)] * 2


def test_http_disconnect_keeps_the_run_going(chat):
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
        assert started.is_set()
        await asyncio.to_thread(wait_for, lambda: row(chat, data.run_id)["status"] == "finished")
        assert chat["app"].state.engine.pool.checkedout() == 0
        with chat["owner"].connect() as connection:
            stored = connection.execute(
                select(m.metadata.tables["agent_sessions"].c.state).where(
                    m.metadata.tables["agent_sessions"].c.conversation_id == data.thread_id
                )
            ).scalar_one()
        assert bool(stored)

    asyncio.run(execute())
