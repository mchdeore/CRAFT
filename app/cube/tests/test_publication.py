"""Production approval, source validation and atomic factual cube publication."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from threading import Event, local
from uuid import uuid4

import msgspec
import pytest
from agent_framework import ChatResponse, Content, Message
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import create_app
from app.agent import model
from app.agent import service as agent_service
from app.agent.handlers import RunAgentInput
from app.approvals.handlers import (
    ApprovalRef,
    ApprovalRequest,
    Decision,
    approved_preparation,
    decide_approval,
    read_approval,
    request_approval,
)
from app.cube import publication, storage
from app.cube.handlers import (
    PrepareValues,
    SubmitValues,
    ValueRef,
    get_value_evidence,
    prepare_values,
    submit_values,
)
from app.cube.tests.test_intake import intake_source as intake_source
from app.cube.tests.test_intake import table
from app.infrastructure.database import metadata as m
from app.infrastructure.database.unit_of_work import has_ambient_transaction
from app.infrastructure.errors import ApiError
from app.infrastructure.mcp.tests.http_fixture import live_server
from app.infrastructure.operations.pipeline import ApprovalRequired
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library.handlers import index_provider
from app.tests.support.model import ScriptedChatClient


@pytest.fixture
def publication_source(intake_source):
    source = intake_source
    with source["owner"].begin() as connection:
        conversation = connection.execute(
            m.metadata.tables["conversations"]
            .insert()
            .values(owner_id=source["caller"].user_id, title="Generated cube review")
            .returning(m.metadata.tables["conversations"].c.id)
        ).scalar_one()
    run = uuid4()
    agent_service.start(
        source["caller"],
        RunAgentInput(
            thread_id=conversation,
            run_id=run,
            messages=[{"role": "user", "content": "Review generated source values."}],
        ),
        "publication-fixture",
    )
    source = source | {"conversation": conversation, "run": run}
    try:
        yield source
    finally:
        with source["owner"].begin() as connection:
            connection.execute(
                m.cube_values.delete().where(m.cube_values.c.mission_key == source["mission"])
            )
            requests = m.metadata.tables["approval_requests"]
            ids = select(requests.c.id).where(requests.c.conversation_id == conversation)
            connection.execute(
                m.metadata.tables["approval_events"]
                .delete()
                .where(m.metadata.tables["approval_events"].c.request_id.in_(ids))
            )
            connection.execute(requests.delete().where(requests.c.conversation_id == conversation))
            for name in ("messages", "agent_sessions", "agent_runs"):
                target = m.metadata.tables[name]
                connection.execute(target.delete().where(target.c.conversation_id == conversation))
            connection.execute(
                m.metadata.tables["conversations"]
                .delete()
                .where(m.metadata.tables["conversations"].c.id == conversation)
            )


def values(source):
    handle = table(
        source,
        ["Element", "Dry mass (g)", "Peak power (W)"],
        [["Mission total", "450.123456789012345678", "24..30"], ["Payload", "TBD", "n/a"]],
    )
    prepared = prepare_values(
        source["caller"], PrepareValues(handle=handle, valid_from="2026-01-01")
    )
    assert not prepared.problems and all(
        candidate.problem is None for candidate in prepared.candidates
    )
    return SubmitValues(
        mission=prepared.mission,
        silo=prepared.silo,
        stage=prepared.stage,
        valid_from=prepared.valid_from,
        candidates=prepared.candidates,
    )


def approval(source, request, *, approve=True):
    caller = replace(
        source["caller"], origin="mcp", run_id=source["run"], conversation_id=source["conversation"]
    )
    proposal = request_approval(
        caller,
        ApprovalRequest(
            run_id=source["run"],
            conversation_id=source["conversation"],
            operation=submit_values.__operation__.name,
            arguments=msgspec.to_builtins(request),
            evidence=[],
            reasoning="Publish the generated source cells as one reviewed batch.",
            retrieval_path=[],
            interrupt_id="generated-cube-batch",
        ),
    )
    decide_approval(
        replace(source["caller"], origin="http"), Decision(id=proposal.id, approve=approve)
    )
    return caller, proposal.id


def stored(source):
    with source["owner"].connect() as connection:
        return list(
            connection.execute(
                select(m.cube_values).where(m.cube_values.c.mission_key == source["mission"])
            ).mappings()
        )


def test_approved_source_batch_keeps_decimal_states_and_receipt_once(publication_source):
    source = publication_source
    request = values(source)
    assert stored(source) == []
    caller, identifier = approval(source, request)
    assert stored(source) == []
    result = submit_values(caller, request)
    rows = stored(source)
    assert result.approval_id == identifier and set(result.value_ids) == {row["id"] for row in rows}
    assert len(rows) == 4 and all(row["approval_request_id"] == identifier for row in rows)
    exact = next(row for row in rows if row["kind"] == "exact")
    assert exact["low"] == exact["high"] == Decimal("0.450123456789012345678")
    assert exact["source_number"] == "450.123456789012345678" and exact["source_unit"] == "g"
    evidence = get_value_evidence(source["caller"], ValueRef(id=exact["id"]))
    assert evidence.value.approval_id == identifier and evidence.citation_status == "current"
    assert "0.450123456789012345678 kg" in evidence.conversion
    ranged = next(row for row in rows if row["kind"] == "range")
    assert (ranged["low"], ranged["high"]) == (Decimal(24), Decimal(30))
    assert {row["kind"] for row in rows if row["low"] is None} == {"unknown", "not_applicable"}
    for candidate in request.candidates:
        row = next(
            row
            for row in rows
            if row["locator"] == candidate.citation.locator
            and row["parameter"] == candidate.parameter
        )
        assert row["quote_sha256"] == bytes.fromhex(candidate.citation.quote_sha256)
        assert (row["item"], row["version"], row["block"], row["simulated"]) == (
            candidate.citation.item,
            candidate.citation.version,
            candidate.citation.block,
            candidate.citation.simulated,
        )
    with pytest.raises(ApprovalRequired):
        submit_values(caller, request)
    assert len(stored(source)) == 4
    assert read_approval(source["caller"], ApprovalRef(id=identifier)).status == "executed"


def test_rejected_batch_has_no_effect(publication_source):
    source = publication_source
    request = values(source)
    caller, identifier = approval(source, request, approve=False)
    with pytest.raises(ApprovalRequired):
        submit_values(caller, request)
    assert not stored(source)
    assert read_approval(source["caller"], ApprovalRef(id=identifier)).status == "rejected"


def test_registered_publication_prepares_outside_orm_transactions(publication_source, monkeypatch):
    source = publication_source
    request = values(source)
    caller, identifier = approval(source, request)
    active, seen = set(), []

    def started(session, transaction):
        active.add(transaction)

    def ended(session, transaction):
        active.discard(transaction)

    def inspected(name, original):
        def read(*args, **kwargs):
            assert not active and not has_ambient_transaction()
            seen.append(name)
            return original(*args, **kwargs)

        return read

    for name in ("get_item", "read_content", "items"):
        monkeypatch.setattr(
            SimulatedProvider, name, inspected(name, getattr(SimulatedProvider, name))
        )
    event.listen(Session, "after_transaction_create", started)
    event.listen(Session, "after_transaction_end", ended)
    try:
        result = submit_values(caller, request)
    finally:
        event.remove(Session, "after_transaction_create", started)
        event.remove(Session, "after_transaction_end", ended)
    assert result.approval_id == identifier and len(result.value_ids) == 4
    assert {"get_item", "read_content"} <= set(seen) and not active
    assert read_approval(source["caller"], ApprovalRef(id=identifier)).status == "executed"


@pytest.mark.parametrize("change", ["version_hash", "block_text", "latest", "grant", "metadata"])
def test_commit_revalidates_current_index_and_grants(publication_source, change, monkeypatch):
    source = publication_source
    request = values(source)
    caller, identifier = approval(source, request)
    prepare = publication.prepare_submission

    def changed_after_preflight(caller, request):
        prepared = prepare(caller, request)
        evidence = prepared.sources[0]
        if change == "latest":
            source["provider"].write_content(source["path"], b"# New indexed source after review\n")
            index_provider(source["settings"])
        else:
            with source["owner"].begin() as connection:
                if change == "version_hash":
                    mutation = (
                        m.doc_versions.update()
                        .where(m.doc_versions.c.id == evidence.version_id)
                        .values(sha256=b"\x00" * 32)
                    )
                elif change == "block_text":
                    mutation = (
                        m.chunks.update()
                        .where(m.chunks.c.id == evidence.block_id)
                        .values(text="Changed indexed source text after review")
                    )
                elif change == "metadata":
                    mutation = (
                        m.provider_items.update()
                        .where(m.provider_items.c.item == evidence.item)
                        .values(title="Changed indexed title after review")
                    )
                else:
                    mutation = m.mission_grants.delete().where(
                        m.mission_grants.c.user_id == caller.user_id
                    )
                connection.execute(mutation)
        return prepared

    monkeypatch.setattr(publication, "prepare_submission", changed_after_preflight)
    with pytest.raises(ApiError):
        submit_values(caller, request)
    assert not stored(source)
    assert read_approval(source["caller"], ApprovalRef(id=identifier)).status == "failed"
    with pytest.raises(ApprovalRequired):
        submit_values(caller, request)


def test_publication_refuses_wrong_preparation_type(publication_source, monkeypatch):
    source = publication_source
    request = values(source)
    caller, identifier = approval(source, request)
    monkeypatch.setattr(publication, "prepare_submission", lambda caller, request: object())
    with pytest.raises(ApiError):
        submit_values(caller, request)
    assert not stored(source)
    assert read_approval(source["caller"], ApprovalRef(id=identifier)).status == "failed"


def test_private_preparation_is_bound_to_call_and_reset(publication_source, monkeypatch):
    source = publication_source
    request = values(source)
    caller, identifier = approval(source, request)
    submit = publication.submit
    operation = submit_values.__operation__.name

    def checked(caller, request):
        for changed in (
            replace(caller, user_id=uuid4()),
            replace(caller, run_id=uuid4()),
            replace(caller, conversation_id=uuid4()),
            replace(caller, tool_call_id="foreign"),
            replace(caller, origin="python"),
            replace(caller, sessions=None),
        ):
            with pytest.raises(ApprovalRequired):
                approved_preparation(changed, operation, request)
        with pytest.raises(ApprovalRequired):
            approved_preparation(caller, "foreign.operation", request)
        with pytest.raises(ApprovalRequired):
            approved_preparation(
                caller, operation, msgspec.structs.replace(request, valid_from="2026-02-01")
            )
        bound, prepared = approved_preparation(caller, operation, request)
        assert bound == identifier and type(prepared) is publication.PreparedPublication
        return submit(caller, request)

    monkeypatch.setattr(publication, "submit", checked)
    assert submit_values(caller, request).approval_id == identifier
    with pytest.raises(ApprovalRequired):
        approved_preparation(caller, operation, request)


@pytest.mark.parametrize(
    "change", ["number", "parameter", "quote", "simulated", "problem", "duplicate"]
)
def test_approved_client_candidate_cannot_forge_a_fact(publication_source, change):
    source = publication_source
    request = values(source)
    candidate = request.candidates[0]
    if change == "duplicate":
        request.candidates.append(candidate)
    elif change == "quote":
        request.candidates[0] = msgspec.structs.replace(
            candidate,
            citation=msgspec.structs.replace(candidate.citation, quote_sha256="0" * 64),
        )
    elif change == "simulated":
        request.candidates[0] = msgspec.structs.replace(
            candidate, citation=msgspec.structs.replace(candidate.citation, simulated=False)
        )
    else:
        candidate = msgspec.structs.replace(
            candidate,
            **{
                "number": {"source_number": "451", "low": 0.451, "high": 0.451},
                "parameter": {"parameter": "power_peak"},
                "problem": {"problem": "outside the allowed bounds"},
            }[change],
        )
        request.candidates[0] = candidate
    caller, identifier = approval(source, request)
    with pytest.raises(ApiError):
        submit_values(caller, request)
    assert not stored(source)
    assert read_approval(source["caller"], ApprovalRef(id=identifier)).status == "failed"


@pytest.mark.parametrize("change", ["version", "withdrawal", "grant"])
def test_source_and_permission_changes_invalidate_approved_batch(publication_source, change):
    source = publication_source
    request = values(source)
    caller, identifier = approval(source, request)
    if change == "version":
        source["provider"].write_content(source["path"], b"# New generated source version\n")
    elif change == "withdrawal":
        source["provider"].delete_item(source["item"])
    else:
        with source["owner"].begin() as connection:
            connection.execute(
                m.mission_grants.delete().where(m.mission_grants.c.user_id == caller.user_id)
            )
    with pytest.raises(ApiError):
        submit_values(caller, request)
    assert not stored(source)
    assert read_approval(source["caller"], ApprovalRef(id=identifier)).status == "failed"


def test_partial_insert_failure_rolls_back_entire_batch(publication_source, monkeypatch):
    source = publication_source
    request = values(source)
    caller, identifier = approval(source, request)
    append = storage.append_values

    def fail(session, rows):
        append(session, rows[:1])
        raise RuntimeError("generated storage interruption")

    monkeypatch.setattr(storage, "append_values", fail)
    with pytest.raises(ApiError) as failure:
        submit_values(caller, request)
    assert failure.value.status == 500
    assert "generated storage interruption" not in failure.value.message
    assert not stored(source)
    assert read_approval(source["caller"], ApprovalRef(id=identifier)).status == "failed"
    with pytest.raises(ApprovalRequired):
        submit_values(caller, request)


def test_concurrent_resume_consumes_batch_once(publication_source):
    source = publication_source
    request = values(source)
    caller, identifier = approval(source, request)

    def execute():
        try:
            return submit_values(caller, request)
        except ApprovalRequired:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: execute(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(stored(source)) == 4
    assert read_approval(source["caller"], ApprovalRef(id=identifier)).status == "executed"


def test_losing_preflight_failure_does_not_append_failed_receipt(publication_source, monkeypatch):
    source = publication_source
    request = values(source)
    caller, identifier = approval(source, request)
    prepare = publication.prepare_submission
    failing_started, committed, attempt = Event(), Event(), local()

    def preflight(caller, request):
        prepared = prepare(caller, request)
        if attempt.failing:
            failing_started.set()
            assert committed.wait(10)
            raise RuntimeError("generated losing preparation interruption")
        return prepared

    def execute(failing):
        attempt.failing = failing
        if not failing:
            assert failing_started.wait(10)
        try:
            return submit_values(caller, request)
        except ApprovalRequired:
            return None
        finally:
            if not failing:
                committed.set()

    monkeypatch.setattr(publication, "prepare_submission", preflight)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(execute, [True, False]))
    winner = results[1]
    assert results[0] is None and winner is not None
    assert winner.approval_id == identifier
    assert len(stored(source)) == 4
    with source["owner"].connect() as connection:
        events = m.metadata.tables["approval_events"]
        terminal = list(
            connection.execute(
                select(events.c.kind).where(
                    events.c.request_id == identifier, events.c.kind.in_(["executed", "failed"])
                )
            ).scalars()
        )
    assert terminal == ["executed"]


def test_http_cannot_bypass_requester_review(publication_source):
    source = publication_source
    request = msgspec.to_builtins(values(source))
    with TestClient(source["app"], base_url="https://testserver") as http:
        http.headers["Origin"] = "https://testserver"
        assert http.post("/api/cube/values", json=request).status_code == 401
        assert source["sign_in"](http).status_code == 200
        for candidates in ([], request["candidates"] * 26):
            assert (
                http.post("/api/cube/values", json=request | {"candidates": candidates}).status_code
                == 400
            )
        assert http.post("/api/cube/values", json=request).status_code == 409
    assert not stored(source)


@pytest.mark.parametrize("approve", [True, False])
def test_native_mcp_batch_resumes_after_application_restart(
    publication_source, monkeypatch, approve
):
    source = publication_source
    request = values(source)

    class BatchClient(ScriptedChatClient):
        def reply(self, messages):
            completed = any(
                content.type == "function_result"
                for message in messages
                for content in message.contents
            )
            content = (
                Content.from_text("Generated cube decision processed.")
                if completed
                else Content.from_function_call(
                    call_id="generated-cube-call",
                    name="submit_values",
                    arguments=msgspec.to_builtins(request),
                )
            )
            return ChatResponse(messages=[Message(role="assistant", contents=[content])])

    monkeypatch.setattr(model, "client", lambda _: BatchClient())
    with source["owner"].begin() as connection:
        runs = m.metadata.tables["agent_runs"]
        connection.execute(
            runs.update().where(runs.c.id == source["run"]).values(status="finished")
        )
    identifier = uuid4()
    body = {
        "threadId": str(source["conversation"]),
        "runId": str(identifier),
        "messages": [{"role": "user", "content": "Review the generated source batch."}],
    }

    @contextmanager
    def client():
        app = create_app(source["settings"])
        try:
            with live_server(app), TestClient(app, base_url="https://testserver") as http:
                http.headers["Origin"] = "https://testserver"
                assert source["sign_in"](http).status_code == 200
                yield http
        finally:
            app.state.engine.dispose()

    def events(response):
        assert response.status_code == 200
        return [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]

    with client() as http:
        first = events(http.post("/api/agent/runs", json=body))
        assert first[-1]["outcome"]["type"] == "interrupt"
        pending = http.get("/api/approvals?status=pending").json()[0]
        assert pending["runId"] == str(identifier)
        assert not stored(source)
    with client() as http:
        assert (
            http.post(
                "/api/approvals/" + pending["id"] + "/decision", json={"approve": approve}
            ).status_code
            == 200
        )
        resume = {
            "threadId": body["threadId"],
            "runId": body["runId"],
            "messages": [],
            "resume": {"approvalId": pending["id"]},
        }
        result = events(http.post("/api/agent/runs", json=resume))
        assert result[-1]["type"] == "RUN_FINISHED"
        assert len(stored(source)) == (4 if approve else 0)
        assert http.post("/api/agent/runs", json=resume).status_code in (404, 409)
        assert len(stored(source)) == (4 if approve else 0)
        assert http.get("/api/approvals/" + pending["id"]).json()["status"] == (
            "executed" if approve else "rejected"
        )
