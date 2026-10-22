"""Authenticated, read-only durable recovery from real SQL and native pauses."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB

from app import create_app
from app.agent import handlers, model, storage
from app.agent.tests.test_approval_resume import (
    WriteClient,
    clear_run_approvals,
    events,
    runtime_client,
)
from app.approvals import service as approval_service
from app.approvals.tests.test_decisions import context as context
from app.approvals.tests.test_decisions import login
from app.infrastructure.access import SIGNED_IN
from app.infrastructure.database.metadata import metadata
from app.infrastructure.errors import NotFound

FIELDS = {"status", "runId", "startedAt", "endedAt", "approvalId", "approvalStatus", "canResume"}
IDLE = {
    "status": "none",
    "runId": None,
    "startedAt": None,
    "endedAt": None,
    "approvalId": None,
    "approvalStatus": None,
    "canResume": False,
}
OLD_START = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture
def recovery_http(context):
    app = create_app(context["settings"])
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.headers["Origin"] = "https://testserver"
            login(http, context)
            yield http
    finally:
        app.state.engine.dispose()


@pytest.fixture
def native_pause(context, monkeypatch):
    """Obtain the actual framework serialization, never invent a paused snapshot."""
    monkeypatch.setattr(model, "client", lambda _: WriteClient())
    runs = metadata.tables["agent_runs"]
    with context["owner"].begin() as connection:
        connection.execute(
            runs.update().where(runs.c.id == context["run"]).values(status="finished")
        )
    identifier = uuid4()
    body = {
        "threadId": str(context["conversation"]),
        "runId": str(identifier),
        "messages": [{"role": "user", "content": "Store the generated fixture calculation."}],
    }
    try:
        with runtime_client(context) as http:
            outcome = events(http.post("/api/agent/runs", json=body))[-1]
            assert outcome["outcome"]["type"] == "interrupt"
            approval = http.get("/api/approvals?status=pending").json()[0]
            assert approval["runId"] == str(identifier)
        with context["owner"].connect() as connection:
            sessions = metadata.tables["agent_sessions"]
            state = connection.execute(
                select(sessions.c.state).where(
                    sessions.c.conversation_id == context["conversation"]
                )
            ).scalar_one()
            requests = metadata.tables["approval_requests"]
            request = dict(
                connection.execute(select(requests).where(requests.c.id == UUID(approval["id"])))
                .mappings()
                .one()
            )
        pending = state["state"]["craftApprovals"]
        assert len(pending) == 1
        assert pending[0]["id"] == approval["id"]
        assert request["interrupt_id"] == f"{identifier}:{pending[0]['interrupt']}"
        yield {
            "run": identifier,
            "approval": UUID(approval["id"]),
            "state": deepcopy(state),
            "request": request,
            "body": body,
        }
    finally:
        clear_run_approvals(context, identifier)


def durable_rows(context):
    """Compare all durable inputs and business effects across recovery reads."""
    with context["owner"].connect() as connection:
        result = {}
        for name in ("agent_runs", "agent_sessions", "messages", "approval_requests"):
            table = metadata.tables[name]
            result[name] = [
                dict(row)
                for row in connection.execute(
                    select(table)
                    .where(table.c.conversation_id == context["conversation"])
                    .order_by(table.c.id if "id" in table.c else table.c.conversation_id)
                ).mappings()
            ]
        requests = metadata.tables["approval_requests"]
        approval_events = metadata.tables["approval_events"]
        result["approval_events"] = [
            dict(row)
            for row in connection.execute(
                select(approval_events)
                .where(
                    approval_events.c.request_id.in_(
                        select(requests.c.id).where(
                            requests.c.conversation_id == context["conversation"]
                        )
                    )
                )
                .order_by(approval_events.c.id)
            ).mappings()
        ]
        notes = metadata.tables["notes"]
        result["notes"] = [
            dict(row)
            for row in connection.execute(
                select(notes).where(notes.c.owner == context["users"][0][0]).order_by(notes.c.id)
            ).mappings()
        ]
        return result


@contextmanager
def metadata_only(http, monkeypatch):
    """Fail if a GET loads native JSON, locks a run or enters execution paths."""

    def forbidden(*args, **kwargs):
        raise AssertionError("recovery reads must not execute, resume, repair or load native state")

    def projected(connection, cursor, statement, parameters, execution_context, many):
        compiled = getattr(execution_context, "compiled", None)
        selected = getattr(getattr(compiled, "statement", None), "selected_columns", ())
        for column in selected:
            if "agent_sessions.state" in str(column):
                assert not isinstance(column.type, JSONB), "full native JSON/subtree was selected"
        if "agent_runs" in statement or "agent_sessions" in statement:
            assert "for update" not in statement.lower(), "read boundary locked runtime rows"

    engine = http.app.state.engine
    with monkeypatch.context() as patches:
        for name in ("start", "resume", "finish", "paused_state"):
            patches.setattr(storage, name, forbidden)
        patches.setattr(model, "client", forbidden)
        patches.setattr(approval_service, "execute_approved", forbidden)
        event.listen(engine, "before_cursor_execute", projected)
        try:
            yield
        finally:
            event.remove(engine, "before_cursor_execute", projected)


def read_recovery(http, context):
    response = http.get(f"/api/conversations/{context['conversation']}/run")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == FIELDS
    assert isinstance(data["canResume"], bool)
    return data


def repeated_read(http, context, monkeypatch):
    before = durable_rows(context)
    with metadata_only(http, monkeypatch):
        first = read_recovery(http, context)
        assert read_recovery(http, context) == first
    assert durable_rows(context) == before
    return first


def update_run(context, **values):
    runs = metadata.tables["agent_runs"]
    with context["owner"].begin() as connection:
        connection.execute(runs.update().where(runs.c.id == context["run"]).values(**values))


def insert_run(context, identifier, status, started_at):
    with context["owner"].begin() as connection:
        connection.execute(
            metadata.tables["agent_runs"]
            .insert()
            .values(
                id=identifier,
                conversation_id=context["conversation"],
                started_by=context["users"][0][0],
                status=status,
                model="generated-private-model-marker",
                instructions_version="generated-private-version-marker",
                started_at=started_at,
                ended_at=started_at + timedelta(seconds=1),
            )
        )


def test_recovery_handler_is_authenticated_metadata_get():
    handler = getattr(handlers, "get_run_recovery", None)
    assert handler is not None, "owned metadata recovery operation is absent"
    declaration = handler.__operation__
    assert declaration.access == SIGNED_IN
    assert declaration.method == "GET"
    assert declaration.path == "/conversations/{id}/run"
    assert declaration.tool is False
    assert declaration.effects == "NONE"
    assert declaration.input.__name__ == "RunRecoveryRef"
    assert declaration.output.__name__ == "RunRecovery"


def test_recovery_requires_current_authenticated_session(context, recovery_http):
    recovery_http.cookies.clear()
    response = recovery_http.get(f"/api/conversations/{context['conversation']}/run")
    assert response.status_code == 401


@pytest.mark.parametrize("denied", ["missing", "foreign", "archived", "disabled"])
def test_recovery_denies_unavailable_or_unowned_conversation(context, recovery_http, denied):
    identifier = context["conversation"]
    if denied == "missing":
        identifier = uuid4()
    elif denied == "foreign":
        login(recovery_http, context, index=1)
    else:
        table = metadata.tables["conversations" if denied == "archived" else "users"]
        with context["owner"].begin() as connection:
            connection.execute(
                table.update()
                .where(
                    table.c.id == (identifier if denied == "archived" else context["users"][0][0])
                )
                .values(
                    **{
                        ("archived_at" if denied == "archived" else "disabled_at"): datetime.now(
                            UTC
                        )
                    }
                )
            )
    response = recovery_http.get(f"/api/conversations/{identifier}/run")
    assert response.status_code == (401 if denied == "disabled" else 404)
    assert "runId" not in response.json()


@pytest.mark.parametrize("denied", ["archived", "disabled"])
def test_recovery_direct_entry_rechecks_current_sql_ownership(context, denied):
    handler = getattr(handlers, "get_run_recovery", None)
    reference = getattr(handlers, "RunRecoveryRef", None)
    assert handler is not None and reference is not None
    table = metadata.tables["conversations" if denied == "archived" else "users"]
    identifier = context["conversation"] if denied == "archived" else context["users"][0][0]
    with context["owner"].begin() as connection:
        connection.execute(
            table.update()
            .where(table.c.id == identifier)
            .values(**{"archived_at" if denied == "archived" else "disabled_at": datetime.now(UTC)})
        )
    with pytest.raises(NotFound):
        handler(context["caller"], reference(id=context["conversation"]))


def test_recovery_idle_is_explicit_and_read_only(context, recovery_http, monkeypatch):
    runs = metadata.tables["agent_runs"]
    with context["owner"].begin() as connection:
        connection.execute(runs.delete().where(runs.c.id == context["run"]))
    assert repeated_read(recovery_http, context, monkeypatch) == IDLE


@pytest.mark.parametrize("status", ["running", "paused", "finished", "failed", "cancelled"])
def test_recovery_projects_every_recorded_status_without_liveness_inference(
    context, recovery_http, monkeypatch, status
):
    ended_at = None if status == "running" else OLD_START + timedelta(seconds=7)
    update_run(
        context,
        status=status,
        started_at=OLD_START,
        ended_at=ended_at,
        model="generated-private-model-marker",
        instructions_version="generated-private-version-marker",
        token_sha256=b"generated-private-token-marker!!",
        token_expires_at=OLD_START + timedelta(minutes=1),
    )
    data = repeated_read(recovery_http, context, monkeypatch)
    assert data == {
        "status": status,
        "runId": str(context["run"]),
        "startedAt": OLD_START.isoformat(),
        "endedAt": ended_at.isoformat() if ended_at else None,
        "approvalId": None,
        "approvalStatus": None,
        "canResume": False,
    }


def test_recovery_does_not_disclose_foreign_started_run(context, recovery_http, monkeypatch):
    update_run(context, started_by=context["users"][1][0])
    assert repeated_read(recovery_http, context, monkeypatch) == IDLE


@pytest.mark.parametrize("active", ["running", "paused"])
def test_recovery_active_first_then_latest_start_then_uuid(
    context, recovery_http, monkeypatch, active
):
    update_run(context, status="finished", started_at=OLD_START + timedelta(days=3))
    identifiers = sorted([uuid4(), uuid4(), uuid4()])
    insert_run(context, identifiers[2], active, OLD_START)
    insert_run(context, identifiers[0], active, OLD_START + timedelta(days=1))
    insert_run(context, identifiers[1], active, OLD_START + timedelta(days=1))
    data = repeated_read(recovery_http, context, monkeypatch)
    assert data["runId"] == str(identifiers[1])
    assert data["status"] == active
    assert data["startedAt"] == (OLD_START + timedelta(days=1)).isoformat()
    assert data["endedAt"] == (OLD_START + timedelta(days=1, seconds=1)).isoformat()


def test_recovery_terminal_history_latest_start_then_uuid(context, recovery_http, monkeypatch):
    update_run(context, status="cancelled", started_at=OLD_START)
    identifiers = sorted([uuid4(), uuid4(), uuid4()])
    insert_run(context, identifiers[2], "finished", OLD_START + timedelta(seconds=1))
    insert_run(context, identifiers[0], "failed", OLD_START + timedelta(seconds=2))
    insert_run(context, identifiers[1], "cancelled", OLD_START + timedelta(seconds=2))
    data = repeated_read(recovery_http, context, monkeypatch)
    assert data["runId"] == str(identifiers[1])
    assert data["status"] == "cancelled"


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "executed", "failed"])
def test_native_pause_recovery_after_reload_projects_bound_approval_without_replay(
    context, native_pause, monkeypatch, status
):
    # runtime_client constructs a fresh app, engine and authenticated connection.
    with runtime_client(context) as http:
        if status != "pending":
            response = http.post(
                f"/api/approvals/{native_pause['approval']}/decision",
                json={"approve": status != "rejected"},
            )
            assert response.status_code == 200
        if status in {"executed", "failed"}:
            with context["owner"].begin() as connection:
                connection.execute(
                    metadata.tables["approval_events"]
                    .insert()
                    .values(
                        request_id=native_pause["approval"],
                        kind=status,
                        actor_id=context["users"][0][0],
                        at=datetime.now(UTC) + timedelta(seconds=1),
                    )
                )
        data = repeated_read(http, context, monkeypatch)
        assert data["status"] == "paused"
        assert data["runId"] == str(native_pause["run"])
        assert data["approvalId"] == str(native_pause["approval"])
        assert data["approvalStatus"] == status
        assert data["canResume"] is (status in {"approved", "rejected"})
        assert durable_rows(context)["notes"] == []


def malformed_linkages(native_pause):
    state = native_pause["state"]
    pending = state["state"]["craftApprovals"][0]
    cases = [
        ("snapshot-null", None),
        ("snapshot-array", []),
        ("snapshot-text", "invalid"),
        ("state-null", {"state": None}),
        ("state-array", {"state": []}),
        ("approvals-missing", {"state": {}}),
    ]
    for name, value in [
        ("approvals-null", None),
        ("approvals-object", {}),
        ("approvals-text", "invalid"),
        ("approvals-empty", []),
        ("approvals-multiple", [pending, pending]),
        ("approval-null", [None]),
        ("approval-text", ["invalid"]),
        ("approval-array", [[]]),
    ]:
        changed = deepcopy(state)
        changed["state"]["craftApprovals"] = deepcopy(value)
        cases.append((name, changed))
    for name, field, value in [
        ("id-missing", "id", None),
        ("id-not-uuid", "id", "invalid"),
        ("id-not-string", "id", 12),
        ("id-unknown", "id", str(uuid4())),
        ("id-noncanonical", "id", str(native_pause["approval"]).upper()),
        ("interrupt-missing", "interrupt", None),
        ("interrupt-empty", "interrupt", ""),
        ("interrupt-number", "interrupt", 12),
        ("interrupt-wrong", "interrupt", "generated-unmatched-interrupt"),
    ]:
        changed = deepcopy(state)
        if value is None:
            del changed["state"]["craftApprovals"][0][field]
        else:
            changed["state"]["craftApprovals"][0][field] = value
        cases.append((name, changed))
    return cases


@pytest.mark.slow
def test_native_pause_recovery_corrupt_or_missing_linkage_is_hidden_and_read_only(
    context, native_pause, monkeypatch
):
    sessions = metadata.tables["agent_sessions"]
    with runtime_client(context) as http:
        assert (
            http.post(
                f"/api/approvals/{native_pause['approval']}/decision", json={"approve": True}
            ).status_code
            == 200
        )
        assert read_recovery(http, context)["canResume"] is True
        for case, state in malformed_linkages(native_pause):
            with context["owner"].begin() as connection:
                connection.execute(
                    sessions.update()
                    .where(sessions.c.conversation_id == context["conversation"])
                    .values(state=state)
                )
            data = repeated_read(http, context, monkeypatch)
            assert (data["approvalId"], data["approvalStatus"], data["canResume"]) == (
                None,
                None,
                False,
            ), case
            assert data["status"] == "paused", case
            assert data["runId"] == str(native_pause["run"]), case
        with context["owner"].begin() as connection:
            connection.execute(
                sessions.delete().where(sessions.c.conversation_id == context["conversation"])
            )
        data = repeated_read(http, context, monkeypatch)
        assert (data["approvalId"], data["approvalStatus"], data["canResume"]) == (
            None,
            None,
            False,
        ), "missing-session"


def test_native_pause_recovery_approval_requester_run_and_conversation_must_match(
    context, native_pause, monkeypatch
):
    requests = metadata.tables["approval_requests"]
    conversations = metadata.tables["conversations"]
    foreign_conversation = uuid4()
    with context["owner"].begin() as connection:
        connection.execute(
            conversations.insert().values(
                id=foreign_conversation,
                owner_id=context["users"][1][0],
                title="Generated foreign fixture",
            )
        )
    try:
        with runtime_client(context) as http:
            assert (
                http.post(
                    f"/api/approvals/{native_pause['approval']}/decision", json={"approve": True}
                ).status_code
                == 200
            )
            for field, value in [
                ("requested_by", context["users"][1][0]),
                ("run_id", context["run"]),
                ("conversation_id", foreign_conversation),
                ("interrupt_id", "generated-unmatched-approval-interrupt"),
            ]:
                with context["owner"].begin() as connection:
                    connection.execute(
                        requests.update()
                        .where(requests.c.id == native_pause["approval"])
                        .values(**{field: value})
                    )
                data = repeated_read(http, context, monkeypatch)
                assert (data["approvalId"], data["approvalStatus"], data["canResume"]) == (
                    None,
                    None,
                    False,
                ), field
                with context["owner"].begin() as connection:
                    connection.execute(
                        requests.update()
                        .where(requests.c.id == native_pause["approval"])
                        .values(**{field: native_pause["request"][field]})
                    )
    finally:
        with context["owner"].begin() as connection:
            connection.execute(
                requests.update()
                .where(requests.c.id == native_pause["approval"])
                .values(conversation_id=context["conversation"])
            )
            connection.execute(
                conversations.delete().where(conversations.c.id == foreign_conversation)
            )


@pytest.mark.parametrize("approve", [True, False])
def test_native_recovered_decision_still_requires_explicit_authoritative_resume(
    context, native_pause, monkeypatch, approve
):
    with runtime_client(context) as http:
        assert (
            http.post(
                f"/api/approvals/{native_pause['approval']}/decision", json={"approve": approve}
            ).status_code
            == 200
        )
        assert repeated_read(http, context, monkeypatch)["canResume"] is True
        assert durable_rows(context)["notes"] == []
        resume = {
            "threadId": native_pause["body"]["threadId"],
            "runId": native_pause["body"]["runId"],
            "messages": [],
            "resume": {"approvalId": str(native_pause["approval"])},
        }
        wrong = resume | {"resume": {"approvalId": str(uuid4())}}
        assert http.post("/api/agent/runs", json=wrong).status_code in {404, 409}
        assert repeated_read(http, context, monkeypatch)["canResume"] is True
        assert events(http.post("/api/agent/runs", json=resume))[-1]["type"] == "RUN_FINISHED"
        data = repeated_read(http, context, monkeypatch)
        assert data["status"] == "finished"
        assert (data["approvalId"], data["approvalStatus"], data["canResume"]) == (
            None,
            None,
            False,
        )
        assert len(durable_rows(context)["notes"]) == int(approve)
        assert http.post("/api/agent/runs", json=resume).status_code in {404, 409}
