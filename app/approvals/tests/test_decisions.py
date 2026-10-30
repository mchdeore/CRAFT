"""Owned database requests and authenticated HTTP decision behavior."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.approvals.handlers import Decision, decide_approval, request_approval
from app.infrastructure.database.metadata import metadata
from app.infrastructure.errors import Conflict, Forbidden, NotFound
from app.infrastructure.operations.registry import registry
from app.tests.support.approvals import decision_call, latest_auth_session, login


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


def test_status_lists_and_pending_counts_follow_the_paused_run(context):
    from app.approvals.handlers import ApprovalQuery, list_approvals
    from app.conversations.handlers import ConversationRef, list_conversations, read_conversation

    caller = context["caller"]
    first = request_approval(caller, context["proposal"](1, "first-call"))
    second = request_approval(caller, context["proposal"](2, "second-call"))

    def listed(status=None):
        return {row.id: row.status for row in list_approvals(caller, ApprovalQuery(status=status))}

    def counts():
        summary = next(
            row for row in list_conversations(caller) if row.id == context["conversation"]
        )
        detail = read_conversation(caller, ConversationRef(id=context["conversation"]))
        return summary.pending_approvals, detail.pending_approvals

    assert listed("pending") == {first.id: "pending", second.id: "pending"}
    # A running run's requests are not waiting on anyone yet.
    assert counts() == (0, 0)
    runs = metadata.tables["agent_runs"]
    with context["owner"].begin() as db:
        db.execute(runs.update().where(runs.c.id == context["run"]).values(status="paused"))
    assert counts() == (2, 2)
    decide_approval(replace(caller, origin="http"), Decision(id=first.id, approve=True))
    assert listed() == {first.id: "approved", second.id: "pending"}
    assert listed("approved") == {first.id: "approved"}
    assert listed("pending") == {second.id: "pending"}
    assert listed("rejected") == {}
    assert counts() == (1, 1)
    # Once the run stops being paused, its leftover request no longer counts.
    with context["owner"].begin() as db:
        db.execute(runs.update().where(runs.c.id == context["run"]).values(status="failed"))
    assert counts() == (0, 0)
