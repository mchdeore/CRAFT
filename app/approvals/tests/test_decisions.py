"""Owned database requests and authenticated HTTP decision behavior."""

from dataclasses import replace
from uuid import UUID, uuid4

import msgspec
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.agent import service as agent_service
from app.agent.handlers import RunAgentInput
from app.approvals.handlers import ApprovalRequest, Decision, decide_approval, request_approval
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.database.metadata import metadata
from app.infrastructure.errors import Conflict, Forbidden, NotFound
from app.infrastructure.operations import DATA, operation
from app.infrastructure.operations.registry import registry
from app.infrastructure.shapes import Shape
from app.tests.support.chat import agent_worker


class MutationInput(Shape):
    amount: int


@pytest.fixture
def context(settings, owner_engine):
    snapshot = registry.copy()

    @operation(SIGNED_IN, post="/decision-fixture", tool=True, effects=DATA)
    def proposed_mutation(caller: Caller, request: MutationInput) -> None:
        """A generated mutation declaration for approval records."""
        raise AssertionError("requesting a decision must not execute")

    operation_name = proposed_mutation.__operation__.name
    configured = msgspec.structs.replace(settings, allowed_origins="https://testserver")
    app = create_app(configured)
    password = uuid4().hex
    users = []
    with owner_engine.begin() as db:
        for label in ("requester", "foreign"):
            username = label + "-" + uuid4().hex
            user = db.execute(
                metadata.tables["users"]
                .insert()
                .values(username=username, display_name="Generated account")
                .returning(metadata.tables["users"].c.id)
            ).scalar_one()
            users.append((user, username))
            db.execute(metadata.tables["user_roles"].insert().values(user_id=user, role="member"))
            db.execute(
                metadata.tables["local_credentials"]
                .insert()
                .values(user_id=user, password_hash=PasswordHasher().hash(password))
            )
        conversation = db.execute(
            metadata.tables["conversations"]
            .insert()
            .values(owner_id=users[0][0], title="Generated approval conversation")
            .returning(metadata.tables["conversations"].c.id)
        ).scalar_one()
    caller = Caller(
        user_id=users[0][0],
        roles=frozenset({"member"}),
        sessions=app.state.sessions,
        settings=configured,
    )
    run = uuid4()
    data = RunAgentInput(
        thread_id=conversation,
        run_id=run,
        messages=[{"role": "user", "content": "Generated request"}],
    )
    agent_service.start(caller, data, "fixture-version")

    def proposal(amount=12, interrupt="native-call"):
        return ApprovalRequest(
            run_id=run,
            conversation_id=conversation,
            operation=operation_name,
            arguments={"amount": amount},
            evidence=[],
            reasoning="Store a generated amount.",
            retrieval_path=[],
            interrupt_id=interrupt,
        )

    try:
        with agent_worker(owner_engine, configured) as worker:
            yield {
                "caller": caller,
                "proposal": proposal,
                "owner": owner_engine,
                "settings": configured,
                "users": users,
                "password": password,
                "run": run,
                "conversation": conversation,
                "operation": operation_name,
                "worker": worker,
            }
    finally:
        with owner_engine.begin() as db:
            inputs = metadata.tables["agent_run_inputs"]
            events = metadata.tables["agent_run_events"]
            owned = select(inputs.c.run_id).where(inputs.c.owner_id.in_([u for u, _ in users]))
            db.execute(events.delete().where(events.c.run_id.in_(owned)))
            db.execute(inputs.delete().where(inputs.c.owner_id.in_([u for u, _ in users])))
            db.execute(
                metadata.tables["notes"]
                .delete()
                .where(metadata.tables["notes"].c.owner.in_([user for user, _ in users]))
            )
            ids = select(metadata.tables["approval_requests"].c.id).where(
                metadata.tables["approval_requests"].c.run_id == run
            )
            db.execute(
                metadata.tables["approval_events"]
                .delete()
                .where(metadata.tables["approval_events"].c.request_id.in_(ids))
            )
            db.execute(
                metadata.tables["approval_requests"]
                .delete()
                .where(metadata.tables["approval_requests"].c.run_id == run)
            )
            for name in ("messages", "agent_sessions", "agent_runs"):
                db.execute(
                    metadata.tables[name]
                    .delete()
                    .where(metadata.tables[name].c.conversation_id == conversation)
                )
            db.execute(
                metadata.tables["conversations"]
                .delete()
                .where(metadata.tables["conversations"].c.id == conversation)
            )
            for user, _username in users:
                for name in ("auth_sessions", "local_credentials", "user_roles"):
                    db.execute(
                        metadata.tables[name]
                        .delete()
                        .where(metadata.tables[name].c.user_id == user)
                    )
                db.execute(
                    metadata.tables["users"].delete().where(metadata.tables["users"].c.id == user)
                )
        app.state.engine.dispose()
        registry.clear()
        registry.update(snapshot)


def login(http, context, index=0):
    response = http.post(
        "/api/session",
        json={"username": context["users"][index][1], "password": context["password"]},
        headers={"Origin": "https://testserver"},
    )
    assert response.status_code == 200


def decision_call(context, response):
    logs = metadata.tables["call_log"]
    with context["owner"].connect() as db:
        return (
            db.execute(
                select(logs).where(
                    logs.c.request_id == UUID(response.headers["x-request-id"]),
                    logs.c.name == "approvals.decide_approval",
                )
            )
            .mappings()
            .one()
        )


def latest_auth_session(context, index=0):
    sessions = metadata.tables["auth_sessions"]
    with context["owner"].connect() as db:
        return db.execute(
            select(sessions.c.id)
            .where(sessions.c.user_id == context["users"][index][0])
            .order_by(sessions.c.created_at.desc(), sessions.c.id.desc())
            .limit(1)
        ).scalar_one()


def test_frozen_request_is_idempotent_and_changed_arguments_conflict(context):
    first = request_approval(context["caller"], context["proposal"]())
    second = request_approval(context["caller"], context["proposal"]())
    assert first.id == second.id and first.status == "pending"
    with pytest.raises(Conflict):
        request_approval(context["caller"], context["proposal"](13))
    with context["owner"].connect() as db:
        rows = db.execute(
            select(metadata.tables["approval_events"]).where(
                metadata.tables["approval_events"].c.request_id == first.id
            )
        ).all()
        assert len(rows) == 1


def test_http_owner_decides_once_and_foreign_cannot_observe(context):
    approval = request_approval(context["caller"], context["proposal"]())
    app = create_app(context["settings"])
    with TestClient(app, base_url="https://testserver") as http:
        login(http, context)
        auth_session = latest_auth_session(context)
        pending = http.get("/api/approvals?status=pending")
        assert pending.status_code == 200 and any(
            row["id"] == str(approval.id) for row in pending.json()
        )
        detail = http.get("/api/approvals/" + str(approval.id))
        assert detail.json()["arguments"] == {"amount": 12}
        decided = http.post(
            "/api/approvals/" + str(approval.id) + "/decision",
            json={"approve": True, "note": "Generated acceptance"},
            headers={"Origin": "https://testserver"},
        )
        assert decided.status_code == 200 and decided.json()["status"] == "approved"
        recorded = decision_call(context, decided)
        assert recorded["origin"] == "http" and recorded["outcome"] == "ok"
        assert recorded["user_id"] == context["users"][0][0]
        assert recorded["auth_session_id"] == auth_session
        assert recorded["conversation_id"] == context["conversation"]
        assert recorded["run_id"] == context["run"]
        assert recorded["tool_call_id"] is None
        replay = http.post(
            "/api/approvals/" + str(approval.id) + "/decision",
            json={"approve": False},
            headers={"Origin": "https://testserver"},
        )
        assert replay.status_code == 409
        recorded = decision_call(context, replay)
        assert recorded["outcome"] == "error" and recorded["error_code"] == "conflict"
        assert recorded["user_id"] == context["users"][0][0]
        assert recorded["auth_session_id"] == auth_session
        assert recorded["conversation_id"] is None
        assert recorded["run_id"] is None
        assert recorded["tool_call_id"] is None
        assert [
            event["kind"]
            for event in http.get("/api/approvals/" + str(approval.id)).json()["events"]
        ] == ["requested", "approved"]
        login(http, context, 1)
        foreign_session = latest_auth_session(context, 1)
        assert http.get("/api/approvals/" + str(approval.id)).status_code == 404
        assert all(row["id"] != str(approval.id) for row in http.get("/api/approvals").json())
        foreign = http.post(
            "/api/approvals/" + str(approval.id) + "/decision",
            json={"approve": True},
            headers={"Origin": "https://testserver"},
        )
        assert foreign.status_code == 404
        recorded = decision_call(context, foreign)
        assert recorded["outcome"] == "error" and recorded["error_code"] == "not_found"
        assert recorded["user_id"] == context["users"][1][0]
        assert recorded["auth_session_id"] == foreign_session
        assert recorded["conversation_id"] is None
        assert recorded["run_id"] is None
        assert recorded["tool_call_id"] is None


def test_agent_and_private_python_cannot_make_human_decision(context):
    approval = request_approval(context["caller"], context["proposal"]())
    for origin in ("python", "mcp"):
        with pytest.raises(Forbidden):
            decide_approval(
                replace(context["caller"], origin=origin), Decision(id=approval.id, approve=True)
            )
    foreign = replace(context["caller"], user_id=context["users"][1][0], origin="http")
    with pytest.raises(NotFound):
        decide_approval(foreign, Decision(id=approval.id, approve=True))
    assert all(
        not op.tool
        for name, op in registry.items()
        if name
        in {
            "approvals.request_approval",
            "approvals.list_approvals",
            "approvals.read_approval",
            "approvals.decide_approval",
        }
    )


def test_http_rejects_invalid_decision_and_wrong_origin_without_event(context):
    approval = request_approval(context["caller"], context["proposal"]())
    with TestClient(create_app(context["settings"]), base_url="https://testserver") as http:
        login(http, context)
        url = "/api/approvals/" + str(approval.id) + "/decision"
        assert (
            http.post(
                url, json={"approve": "true"}, headers={"Origin": "https://testserver"}
            ).status_code
            == 400
        )
        assert (
            http.post(
                url, json={"approve": True}, headers={"Origin": "https://other.example"}
            ).status_code
            == 403
        )
        assert (
            http.post(
                url, json={"approve": False}, headers={"Origin": "https://testserver"}
            ).json()["status"]
            == "rejected"
        )


def test_concurrent_decisions_append_only_one_result(context):
    from concurrent.futures import ThreadPoolExecutor

    approval = request_approval(context["caller"], context["proposal"]())
    trusted = replace(context["caller"], origin="http")

    def choose(value):
        try:
            return decide_approval(trusted, Decision(id=approval.id, approve=value)).status
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        values = list(executor.map(choose, [True, False]))
    assert values.count("conflict") == 1
    with context["owner"].connect() as db:
        events = (
            db.execute(
                select(metadata.tables["approval_events"].c.kind).where(
                    metadata.tables["approval_events"].c.request_id == approval.id
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 2 and "requested" in events
