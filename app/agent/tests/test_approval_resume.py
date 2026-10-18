"""Real HTTP auth, persisted framework pauses and native MCP approved writes."""

import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from agent_framework import ChatResponse, Content, Message
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.agent import model
from app.agent.fake import FakeChatClient
from app.approvals.tests.test_decisions import context as context
from app.approvals.tests.test_decisions import login
from app.infrastructure.database.metadata import metadata
from app.infrastructure.mcp.tests.http_fixture import live_server


class WriteClient(FakeChatClient):
    def reply(self, messages):
        results = [
            content
            for message in messages
            for content in message.contents
            if content.type == "function_result"
        ]
        content = (
            Content.from_text("Generated write decision processed.")
            if results
            else (
                Content.from_function_call(
                    call_id="generated-note-call",
                    name="write_note",
                    arguments={"title": "Generated approved scratchpad", "body": "12"},
                )
            )
        )
        return ChatResponse(messages=[Message(role="assistant", contents=[content])])


def events(response):
    assert response.status_code == 200
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


@contextmanager
def runtime_client(context):
    app = create_app(context["settings"])
    try:
        with live_server(app), TestClient(app, base_url="https://testserver") as http:
            http.headers["Origin"] = "https://testserver"
            login(http, context)
            yield http
    finally:
        app.state.engine.dispose()


def clear_run_approvals(context, identifier):
    with context["owner"].begin() as connection:
        requests = metadata.tables["approval_requests"]
        ids = select(requests.c.id).where(requests.c.run_id == identifier)
        connection.execute(
            metadata.tables["approval_events"]
            .delete()
            .where(metadata.tables["approval_events"].c.request_id.in_(ids))
        )
        connection.execute(requests.delete().where(requests.c.run_id == identifier))


@pytest.mark.parametrize(
    "approve, exhausted", [(True, None), (False, None), (True, "time"), (True, "calls")]
)
def test_http_paused_write_restores_decision_once_after_reload(
    context, monkeypatch, approve, exhausted
):
    monkeypatch.setattr(model, "client", lambda _: WriteClient())
    with context["owner"].begin() as connection:
        runs = metadata.tables["agent_runs"]
        connection.execute(
            runs.update().where(runs.c.id == context["run"]).values(status="finished")
        )
    identifier = uuid4()
    body = {
        "threadId": str(context["conversation"]),
        "runId": str(identifier),
        "messages": [{"role": "user", "content": "Keep the generated calculation."}],
    }
    try:
        with runtime_client(context) as http:
            first = events(http.post("/api/agent/runs", json=body))
            assert first[-1]["outcome"]["type"] == "interrupt"
            with context["owner"].connect() as connection:
                stored = connection.execute(
                    select(metadata.tables["agent_sessions"].c.state).where(
                        metadata.tables["agent_sessions"].c.conversation_id
                        == context["conversation"]
                    )
                ).scalar_one()
                assert stored["craftAguiSnapshot"]["interrupt"]
            if exhausted:
                stored["state"]["craft"][
                    "activeSeconds" if exhausted == "time" else "toolCalls"
                ] = 120 if exhausted == "time" else 20
                with context["owner"].begin() as connection:
                    sessions = metadata.tables["agent_sessions"]
                    connection.execute(
                        sessions.update()
                        .where(sessions.c.conversation_id == context["conversation"])
                        .values(state=stored)
                    )
            approval = http.get("/api/approvals?status=pending").json()[0]
            assert approval["runId"] == str(identifier)
            resume = {
                "threadId": body["threadId"],
                "runId": body["runId"],
                "messages": [],
                "resume": {"approvalId": approval["id"]},
            }
            assert http.post("/api/agent/runs", json=resume).status_code == 409
            forged = resume | {"resume": {"approvalId": approval["id"], "approved": True}}
            assert http.post("/api/agent/runs", json=forged).status_code == 409
        with runtime_client(context) as http:
            assert (
                http.post(
                    "/api/approvals/" + approval["id"] + "/decision",
                    json={"approve": approve},
                ).status_code
                == 200
            )
            second = events(http.post("/api/agent/runs", json=resume))
            assert second[-1]["type"] == ("RUN_ERROR" if exhausted else "RUN_FINISHED")
            assert "outcome" not in second[-1]
            if exhausted:
                assert second[-1]["code"] == ("run_timeout" if exhausted == "time" else "run_limit")
                with context["owner"].connect() as connection:
                    assert (
                        connection.execute(
                            select(runs.c.status).where(runs.c.id == identifier)
                        ).scalar_one()
                        == "failed"
                    )
            replay = http.post("/api/agent/runs", json=resume)
            assert replay.status_code in (404, 409)
            notes = http.get("/api/notes").json()
            assert len(notes) == int(approve and not exhausted)
            status = http.get("/api/approvals/" + approval["id"]).json()["status"]
            assert status == ("approved" if exhausted else "executed" if approve else "rejected")
    finally:
        clear_run_approvals(context, identifier)


def test_multiple_native_write_proposals_stop_before_approval(context, monkeypatch):
    class DoubleWriteClient(WriteClient):
        def reply(self, messages):
            if any(
                content.type == "function_result"
                for message in messages
                for content in message.contents
            ):
                return super().reply(messages)
            return ChatResponse(
                messages=[
                    Message(
                        role="assistant",
                        contents=[
                            Content.from_function_call(
                                call_id=f"native_write_{index}",
                                name="write_note",
                                arguments={"title": "Generated calculation", "body": "12"},
                            )
                            for index in range(2)
                        ],
                    )
                ],
                response_id=uuid4().hex,
                model="fake",
            )

    monkeypatch.setattr(model, "client", lambda _: DoubleWriteClient())
    runs = metadata.tables["agent_runs"]
    with context["owner"].begin() as connection:
        connection.execute(
            runs.update().where(runs.c.id == context["run"]).values(status="finished")
        )
    identifier = uuid4()
    body = {
        "threadId": str(context["conversation"]),
        "runId": str(identifier),
        "messages": [{"role": "user", "content": "Keep two copies of the calculation."}],
    }
    try:
        with runtime_client(context) as http:
            first = events(http.post("/api/agent/runs", json=body))
            assert first[-1]["type"] == "RUN_ERROR"
            assert first[-1]["code"] == "run_limit"
            assert http.get("/api/approvals?status=pending").json() == []
            assert http.get("/api/notes").json() == []
            with context["owner"].connect() as connection:
                assert (
                    connection.execute(
                        select(runs.c.status).where(runs.c.id == identifier)
                    ).scalar_one()
                    == "failed"
                )
    finally:
        clear_run_approvals(context, identifier)
