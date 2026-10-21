"""History and approval outcomes are enforced by database privileges and indexes."""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.tests.test_grants import assert_grants


@pytest.fixture
def history(owner_engine: Engine) -> Iterator[dict[str, UUID]]:
    with owner_engine.begin() as connection:
        user = connection.execute(
            text("insert into users (username, display_name) values (:name, 'Test') returning id"),
            {"name": "test_" + uuid4().hex},
        ).scalar_one()
        conversation = connection.execute(
            text("insert into conversations (owner_id, title) values (:user, 'Test') returning id"),
            {"user": user},
        ).scalar_one()
        run = connection.execute(
            text("""insert into agent_runs (conversation_id, started_by, status, model,
                     instructions_version) values (:conversation, :user, 'running', 'scripted', '1')
                     returning id"""),
            {"conversation": conversation, "user": user},
        ).scalar_one()
        requests = []
        for _ in range(2):
            requests.append(
                connection.execute(
                    text("""insert into approval_requests (run_id, conversation_id, requested_by,
                      operation, arguments, arguments_sha256, evidence, reasoning, retrieval_path,
                      interrupt_id) values (:run, :conversation, :user, 'test.write', '{}',
                      :digest, '[]', '', '[]', :interrupt) returning id"""),
                    {
                        "run": run,
                        "conversation": conversation,
                        "user": user,
                        "digest": bytes(32),
                        "interrupt": uuid4().hex,
                    },
                ).scalar_one()
            )
    yield {
        "user": user,
        "conversation": conversation,
        "run": run,
        "request": requests[0],
        "other": requests[1],
    }
    with owner_engine.begin() as connection:
        connection.execute(
            text("delete from approval_events where request_id in (:a, :b)"),
            {"a": requests[0], "b": requests[1]},
        )
        connection.execute(text("delete from approval_requests where run_id=:id"), {"id": run})
        connection.execute(
            text("delete from messages where conversation_id=:id"), {"id": conversation}
        )
        connection.execute(text("delete from agent_runs where id=:id"), {"id": run})
        connection.execute(text("delete from conversations where id=:id"), {"id": conversation})
        connection.execute(text("delete from users where id=:id"), {"id": user})


def test_messages_append_only(app: FastAPI, owner_engine: Engine, history: dict[str, UUID]) -> None:
    assert_grants(owner_engine, {"messages": {"INSERT", "SELECT"}})
    with app.state.engine.begin() as connection:
        connection.execute(
            text("""insert into messages (conversation_id, seq, role, content)
                                values (:id, 1, 'user', '{}')"""),
            {"id": history["conversation"]},
        )
        assert (
            connection.execute(
                text("select count(*) from messages where conversation_id=:id"),
                {"id": history["conversation"]},
            ).scalar()
            == 1
        )
    with pytest.raises(DBAPIError), app.state.engine.begin() as connection:
        connection.execute(
            text("""insert into messages (conversation_id, seq, role, content)
                                values (:id, 1, 'user', '{}')"""),
            {"id": history["conversation"]},
        )
    for sql in ["update messages set seq=seq", "delete from messages"]:
        with pytest.raises(DBAPIError, match="permission denied"), app.state.engine.begin() as conn:
            conn.execute(text(sql))


def test_approvals_append_only(app: FastAPI, owner_engine: Engine) -> None:
    assert_grants(
        owner_engine,
        {"approval_requests": {"INSERT", "SELECT"}, "approval_events": {"INSERT", "SELECT"}},
    )
    for table in ["approval_requests", "approval_events"]:
        for sql in [f"update {table} set id=id", f"delete from {table}"]:
            with (
                pytest.raises(DBAPIError, match="permission denied"),
                app.state.engine.begin() as conn,
            ):
                conn.execute(text(sql))


def event(app: FastAPI, request: UUID, kind: str) -> None:
    with app.state.engine.begin() as connection:
        connection.execute(
            text("insert into approval_events (request_id, kind) values (:id, :kind)"),
            {"id": request, "kind": kind},
        )


def test_approval_decided_once(app: FastAPI, history: dict[str, UUID]) -> None:
    event(app, history["request"], "approved")
    with pytest.raises(DBAPIError):
        event(app, history["request"], "rejected")
    event(app, history["other"], "rejected")
    with app.state.engine.connect() as connection:
        assert (
            connection.execute(
                text("select kind from approval_status where request_id=:id"),
                {"id": history["request"]},
            ).scalar()
            == "approved"
        )


def test_approval_execution_outcome_once(app: FastAPI, history: dict[str, UUID]) -> None:
    event(app, history["request"], "executed")
    with pytest.raises(DBAPIError):
        event(app, history["request"], "failed")
    event(app, history["other"], "failed")
