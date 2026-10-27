"""Approved writes use the production operation and transaction boundaries."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import msgspec
import pytest
from sqlalchemy import func, select

from app.approvals.handlers import (
    ApprovalRef,
    ApprovalRequest,
    Decision,
    decide_approval,
    read_approval,
    request_approval,
)
from app.approvals.tests.test_decisions import context as context
from app.infrastructure.database.metadata import metadata
from app.infrastructure.errors import ApiError
from app.infrastructure.operations.pipeline import ApprovalRequired
from app.notes import storage as notes_storage
from app.notes.handlers import WriteNote, write_note


def execute_write(caller, value):
    return asyncio.run(write_note(caller, value))


def prepare(context, approved=True, evidence=None):
    value = WriteNote(
        title="Generated approved scratchpad", body="12", conversation_id=context["conversation"]
    )
    trusted = replace(
        context["caller"],
        origin="mcp",
        run_id=context["run"],
        conversation_id=context["conversation"],
    )
    proposal = request_approval(
        trusted,
        ApprovalRequest(
            run_id=context["run"],
            conversation_id=context["conversation"],
            operation=write_note.__operation__.name,
            arguments=msgspec.to_builtins(value),
            evidence=evidence or [],
            reasoning="Keep a generated calculation.",
            retrieval_path=[],
            interrupt_id="generated-write",
        ),
    )
    decide_approval(
        replace(context["caller"], origin="http"), Decision(id=proposal.id, approve=approved)
    )
    return trusted, value, proposal.id


def count_notes(context):
    table = metadata.tables["notes"]
    with context["owner"].connect() as connection:
        return connection.execute(
            select(func.count())
            .select_from(table)
            .where(table.c.owner == context["caller"].user_id)
        ).scalar_one()


def test_approved_write_executes_once_and_changed_arguments_refused(context, monkeypatch):
    caller, value, identifier = prepare(context)
    calls = []
    original = notes_storage.write

    def record(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(notes_storage, "write", record)
    changed = WriteNote(title=value.title, body="13", conversation_id=value.conversation_id)
    with pytest.raises(ApprovalRequired):
        execute_write(caller, changed)
    assert execute_write(caller, value).body == "12"
    with pytest.raises(ApprovalRequired):
        execute_write(caller, value)
    with pytest.raises(ApprovalRequired):
        execute_write(replace(caller, user_id=context["users"][1][0]), value)
    assert calls == [True] and count_notes(context) == 1
    assert read_approval(context["caller"], ApprovalRef(id=identifier)).status == "executed"


def test_distinct_tool_ids_bind_identical_approved_arguments(context, monkeypatch):
    from sqlalchemy.dialects.postgresql import insert

    calls = []
    original = notes_storage.write

    def record(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(notes_storage, "write", record)
    caller, value, first = prepare(context)
    second = request_approval(
        caller,
        ApprovalRequest(
            run_id=context["run"],
            conversation_id=context["conversation"],
            operation=write_note.__operation__.name,
            arguments=msgspec.to_builtins(value),
            evidence=[],
            reasoning="Keep the second generated copy.",
            retrieval_path=[],
            interrupt_id=uuid4().hex,
        ),
    ).id
    decide_approval(replace(context["caller"], origin="http"), Decision(id=second, approve=True))
    sessions = metadata.tables["agent_sessions"]
    state = {
        "state": {
            "craftApprovals": [
                {"id": str(first), "callId": "first"},
                {"id": str(second), "callId": "second"},
            ]
        }
    }
    with context["owner"].begin() as connection:
        connection.execute(
            insert(sessions)
            .values(conversation_id=context["conversation"], state=state)
            .on_conflict_do_update(
                index_elements=[sessions.c.conversation_id], set_={"state": state}
            )
        )
    with pytest.raises(ApprovalRequired):
        execute_write(replace(caller, tool_call_id="foreign"), value)
    for identifier, tool_id in ((first, "first"), (second, "second")):
        bound = replace(caller, tool_call_id=tool_id)
        assert execute_write(bound, value).body == "12"
        assert read_approval(caller, ApprovalRef(id=identifier)).status == "executed"
        with pytest.raises(ApprovalRequired):
            execute_write(bound, value)
    assert len(calls) == 2 and count_notes(context) == 1


def test_rejected_write_and_foreign_caller_never_execute(context):
    caller, value, _ = prepare(context, approved=False)
    with pytest.raises(ApprovalRequired):
        execute_write(caller, value)
    assert count_notes(context) == 0


def test_concurrent_delivery_runs_approved_write_once(context, monkeypatch):
    caller, value, _ = prepare(context)
    calls = []
    original = notes_storage.write

    def record(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(notes_storage, "write", record)

    def execute(_):
        try:
            execute_write(caller, value)
            return "executed"
        except ApprovalRequired:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(execute, range(2))) == ["executed", "refused"]
    assert calls == [True] and count_notes(context) == 1


def test_write_failure_rolls_back_effect_and_records_terminal_failure(context, monkeypatch):
    caller, value, identifier = prepare(context)
    original = notes_storage.write

    def fail(*args):
        original(*args)
        raise RuntimeError("generated failure after insert")

    monkeypatch.setattr(notes_storage, "write", fail)
    with pytest.raises(ApiError):
        execute_write(caller, value)
    assert count_notes(context) == 0
    assert read_approval(context["caller"], ApprovalRef(id=identifier)).status == "failed"
    with pytest.raises(ApprovalRequired):
        execute_write(caller, value)
