"""ask_agent queues a read-only run and returns at once; get_answer reads the cited answer.

The scripted model answers. Holding its reply keeps a run pending; raising fails it.
"""

import time
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.accounts import storage as accounts
from app.accounts.handlers import NewWorkspaceToken, create_token
from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.mcp.tests.test_mcp_tokens import account as account
from app.infrastructure.mcp.tests.test_mcp_tokens import call, logged
from app.infrastructure.operations.storage import call_log
from app.jobs.handlers import requested_job
from app.tests.support.chat import chat as chat
from app.tests.support.cube import cube_source as cube_source
from app.tests.support.model import ScriptedChatClient

events = m.metadata.tables["agent_run_events"]
conversations = m.metadata.tables["conversations"]


def result(response):
    assert response.status_code == 200, response.text
    return response.json()["result"]


def ask(account, raw, **arguments):
    body = result(call(account, raw, "ask_agent", {"question": "Peak power", **arguments}))
    assert body["isError"] is False, body
    return body["structuredContent"]


def answer(account, raw, job):
    return result(call(account, raw, "get_answer", {"jobId": job}))


def answered(account, raw, job, seconds=25):
    # Every poll is a tool call, so it stays well under 60 a minute.
    deadline = time.monotonic() + seconds
    while (body := answer(account, raw, job))["structuredContent"].get("status") == "pending":
        assert time.monotonic() < deadline, "no answer in time"
        time.sleep(0.5)
    return body


def finished(account, job, seconds=25):
    """Wait until the worker has ended the job: its answer can be readable a moment before."""
    deadline = time.monotonic() + seconds
    while (state := account["browser"].get(f"/api/jobs/{job}").json()["status"]) in {
        "queued",
        "scheduled",
        "started",
        "stopping",
    }:
        assert time.monotonic() < deadline, "job did not end in time"
        time.sleep(0.2)
    return state


@pytest.fixture
def stranger(account, owner_engine):
    """Another member, with their own MCP token and conversation."""
    with owner_engine.begin() as connection:
        user = connection.execute(
            accounts.users.insert()
            .values(username="other_" + uuid4().hex, display_name="Other")
            .returning(accounts.users.c.id)
        ).scalar_one()
        connection.execute(accounts.user_roles.insert().values(user_id=user, role="member"))
        conversation = connection.execute(
            conversations.insert()
            .values(owner_id=user, title="Another member's conversation")
            .returning(conversations.c.id)
        ).scalar_one()
    # Issued from the command line, which may manage tokens.
    command_line = Caller(
        user_id=user,
        roles=frozenset({"member"}),
        sessions=account["caller"].sessions,
        settings=account["settings"],
        origin="python",
    )
    token = create_token(command_line, NewWorkspaceToken(label="other", kind="mcp")).token
    yield {"user": user, "token": token, "conversation": conversation}
    with owner_engine.begin() as connection:
        connection.execute(call_log.delete().where(call_log.c.user_id == user))
        connection.execute(conversations.delete().where(conversations.c.owner_id == user))
        for table in [accounts.workspace_tokens, accounts.user_roles]:
            connection.execute(table.delete().where(table.c.user_id == user))
        connection.execute(accounts.users.delete().where(accounts.users.c.id == user))


@pytest.fixture
def held(monkeypatch):
    """The scripted model waits for `set()` before it replies, so the run stays pending."""
    release = Event()
    original = ScriptedChatClient.reply

    def wait(self, messages):
        assert release.wait(30)
        return original(self, messages)

    monkeypatch.setattr(ScriptedChatClient, "reply", wait)
    yield release
    release.set()


def test_ask_agent_returns_a_job_then_get_answer_is_pending_then_cited(account, held):
    raw = account["issue"]()["token"]
    queued = ask(account, raw)
    assert set(queued) == {"jobId", "conversationId"} and queued["jobId"].isdigit()
    assert answer(account, raw, queued["jobId"])["structuredContent"] == {
        "status": "pending",
        "result": None,
    }
    job = account["browser"].get(f"/api/jobs/{queued['jobId']}").json()
    assert job["kind"] == "agent.agent_run" and job["queue"] == "agent"
    assert job["requestedBy"] == str(account["user"])
    listed = account["browser"].get("/api/jobs").json()["jobs"]
    assert queued["jobId"] in {row["jobId"] for row in listed}

    held.set()
    body = answered(account, raw, queued["jobId"])
    assert body["isError"] is False
    final = body["structuredContent"]
    assert final["status"] == "answered"
    assert final["result"]["answer"] and final["result"]["citations"]
    assert final["result"]["confidence"]["level"] in {"high", "medium", "low"}

    assert finished(account, queued["jobId"]) == "finished"
    rows = logged(account, run_id=final["result"]["runId"])
    asked = next(row for row in rows if row["name"] == "agent.ask_agent")
    worker = next(row for row in rows if row["name"] == "agent.agent_run")
    assert asked["origin"] == "mcp" and asked["outcome"] == "ok"
    assert asked["workspace_token_id"] is not None
    assert str(asked["conversation_id"]) == queued["conversationId"]
    assert worker["parent_call_id"] == asked["call_id"]
    reads = logged(account, name="agent.get_answer", workspace_token_id=asked["workspace_token_id"])
    assert len(reads) >= 2 and {row["origin"] for row in reads} == {"mcp"}
    with account["owner"].connect() as connection:
        owner = connection.execute(
            select(conversations.c.owner_id).where(conversations.c.id == queued["conversationId"])
        ).scalar_one()
    assert owner == account["user"]


def test_ask_agent_uses_an_owned_conversation_and_refuses_another(account, stranger):
    raw = account["issue"]()["token"]
    queued = ask(account, raw, conversationId=str(account["thread"]))
    assert queued["conversationId"] == str(account["thread"])
    assert answered(account, raw, queued["jobId"])["structuredContent"]["status"] == "answered"
    finished(account, queued["jobId"])
    for conversation in [stranger["conversation"], uuid4()]:
        refused = result(
            call(
                account,
                raw,
                "ask_agent",
                {"question": "Peak power", "conversationId": str(conversation)},
            )
        )
        assert refused["isError"] and refused["structuredContent"]["code"] == "not_found"


def test_a_failed_run_is_an_error_not_an_answer(account, monkeypatch):
    def fail(self, messages):
        raise RuntimeError("scripted failure")

    monkeypatch.setattr(ScriptedChatClient, "reply", fail)
    raw = account["issue"]()["token"]
    queued = ask(account, raw)
    body = answered(account, raw, queued["jobId"])
    assert body["isError"] is True
    assert body["structuredContent"]["code"] == "internal_error"
    assert "scripted failure" not in str(body)


def test_get_answer_refuses_unknown_malformed_and_other_peoples_jobs(account, stranger):
    raw = account["issue"]()["token"]
    queued = ask(account, raw)
    assert answered(account, raw, queued["jobId"])["structuredContent"]["status"] == "answered"
    assert finished(account, queued["jobId"]) == "finished"
    for job, code in [
        (queued["jobId"], "not_found"),
        ("999999999999", "not_found"),
        ("not-a-job", "invalid_input"),
    ]:
        body = answer(account, stranger["token"], job)
        assert body["isError"] and body["structuredContent"]["code"] == code, job
    assert answer(account, raw, "999999999999")["structuredContent"]["code"] == "not_found"
    # Only agent runs are answers: the same job asked for as another kind is no one's.
    with unit_of_work(account["caller"].sessions, read_only=True) as session:
        job = int(queued["jobId"])
        assert requested_job(session, job, "agent.agent_run", stranger["user"]) is None
        assert requested_job(session, job, "workspace.notebook_run", account["user"]) is None
        found = requested_job(session, job, "agent.agent_run", account["user"])
    assert found is not None and found[0] == "finished"


def test_a_chat_turn_is_not_an_answer(account):
    """A browser chat turn is an agent run too, but get_answer reads only ask_agent's."""
    from app.agent import service
    from app.agent.tests.test_chat_stream import request

    _, job = service.queue_run(account["caller"], request(account), False)
    finished(account, job)
    body = answer(account, account["issue"]()["token"], str(job))
    assert body["isError"] and body["structuredContent"]["code"] == "not_found"


def test_an_answer_whose_events_are_gone_is_not_found(account):
    raw = account["issue"]()["token"]
    queued = ask(account, raw)
    run = answered(account, raw, queued["jobId"])["structuredContent"]["result"]["runId"]
    assert finished(account, queued["jobId"]) == "finished"
    with account["owner"].begin() as connection:
        connection.execute(events.delete().where(events.c.run_id == run))
    body = answer(account, raw, queued["jobId"])
    assert body["isError"] and body["structuredContent"]["code"] == "not_found"


def test_a_browser_session_asks_and_reads_over_http(account):
    asked = account["browser"].post("/api/agent/ask", json={"question": "Peak power"})
    assert asked.status_code == 200, asked.text
    job = asked.json()["jobId"]
    deadline = time.monotonic() + 25
    while (read := account["browser"].get(f"/api/agent/answers/{job}")).json()["status"] == (
        "pending"
    ):
        assert time.monotonic() < deadline
        time.sleep(0.2)
    assert read.status_code == 200 and read.json()["result"]["citations"]
    missing = account["browser"].get("/api/agent/answers/999999999999")
    assert missing.status_code == 404
    assert set(missing.json()) == {"code", "message", "requestId"}
    malformed = account["browser"].get("/api/agent/answers/abc")
    assert malformed.status_code == 400 and malformed.json()["code"] == "invalid_input"
