"""Short owned transactions for frozen actions and requester decisions."""

import hashlib
import inspect
import json
from contextvars import ContextVar
from dataclasses import dataclass, replace
from uuid import UUID

import msgspec
from sqlalchemy.orm import Session, sessionmaker

from app.approvals import storage
from app.infrastructure.access import require
from app.infrastructure.database.unit_of_work import (
    approved_transaction,
    has_ambient_transaction,
    unit_of_work,
)
from app.infrastructure.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.infrastructure.operations.context import current_call
from app.infrastructure.operations.pipeline import decode
from app.infrastructure.operations.registry import registry


@dataclass(frozen=True, slots=True, repr=False)
class _PreparedCall:
    sessions: sessionmaker[Session]
    user_id: UUID
    run_id: UUID
    conversation_id: UUID
    tool_call_id: str | None
    operation: str
    approval_id: UUID
    digest: bytes
    prepared: object


_prepared_call: ContextVar[_PreparedCall | None] = ContextVar(
    "prepared_approved_call", default=None
)


def _sessions(caller):
    if caller.user_id is None or caller.sessions is None:
        raise NotFound()
    return caller.sessions


def _summary(row, status):
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "run_id": row["run_id"],
        "tool": row["operation"].rsplit(".", 1)[-1],
        "summary": row["reasoning"][:300],
        "status": status,
        "requested_at": row["created_at"].isoformat(),
    }


def request(caller, data):
    if caller.origin == "mcp" and (
        caller.run_id != data.run_id or caller.conversation_id != data.conversation_id
    ):
        raise Forbidden()
    declared = registry.get(data.operation)
    if declared is None or declared.effects == "NONE" or not declared.tool:
        raise InvalidInput()
    require(caller, declared.access)
    arguments = msgspec.to_builtins(decode(declared, data.arguments))
    try:
        canonical = json.dumps(
            arguments, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (ValueError, TypeError):
        raise InvalidInput() from None
    if len(canonical) > 65536:
        raise InvalidInput()
    digest = hashlib.sha256(canonical).digest()
    interrupt = str(data.run_id) + ":" + data.interrupt_id
    with unit_of_work(_sessions(caller)) as session:
        storage.require_run(session, caller.user_id, data.run_id, data.conversation_id)
        existing = storage.by_interrupt(session, interrupt)
        if existing is not None:
            if (
                existing["requested_by"],
                existing["run_id"],
                existing["operation"],
                existing["arguments_sha256"],
            ) != (caller.user_id, data.run_id, data.operation, digest):
                raise Conflict()
            return _summary(existing, storage.status(session, existing["id"]))
        row = storage.append_request(session, caller.user_id, data, arguments, digest, interrupt)
        storage.append_event(session, row["id"], "requested", caller.user_id, None)
        return _summary(row, "pending")


def list_owned(caller, status=None):
    with unit_of_work(_sessions(caller), read_only=True) as session:
        rows = storage.list_owned(session, caller.user_id, status)
        values = [_summary(row, row["status"]) for row in rows]
        return [row for row in values if status is None or row["status"] == status][:100]


def read(caller, identifier):
    with unit_of_work(_sessions(caller), read_only=True) as session:
        row = storage.owned(session, caller.user_id, identifier)
        events = storage.events(session, identifier)
        return _summary(row, storage.status(session, identifier)) | {
            "arguments": row["arguments"],
            "evidence": row["evidence"],
            "reasoning": row["reasoning"],
            "retrieval_path": row["retrieval_path"],
            "events": [
                {
                    "kind": event["kind"],
                    "actor_id": event["actor_id"],
                    "at": event["at"].isoformat(),
                    "note": event["note"],
                }
                for event in events
            ],
        }


def decide(caller, data):
    if caller.origin != "http":
        raise Forbidden()
    with unit_of_work(_sessions(caller)) as session:
        row = storage.owned(session, caller.user_id, data.id)
        storage.require_run(session, caller.user_id, row["run_id"], row["conversation_id"])
        if storage.status(session, data.id) != "pending":
            raise Conflict()
        tool_call_id = storage.native_tool_call_id(session, row)
        kind = "approved" if data.approve else "rejected"
        storage.append_event(session, data.id, kind, caller.user_id, data.note)
        declared = registry.get(row["operation"])
        path = row["arguments"].get(declared.grants) if declared and declared.grants else None
        if data.approve and isinstance(path, str):
            storage.add_grant(session, data.id, caller.user_id, row["conversation_id"], path)
        result = _summary(row, kind)
    context = current_call.get()
    if context is not None:
        context.caller = replace(
            caller,
            conversation_id=row["conversation_id"],
            run_id=row["run_id"],
            tool_call_id=tool_call_id,
        )
    return result


def _call_digest(value):
    arguments = msgspec.to_builtins(value)
    return hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).digest()


def require_approved(caller, operation, value):
    from app.infrastructure.operations.pipeline import ApprovalRequired

    if (
        caller.run_id is None
        or caller.conversation_id is None
        or caller.sessions is None
        or caller.user_id is None
        or operation.effects not in {"DATA", "DOCUMENT", "EXTERNAL"}
        or (operation.effects != "EXTERNAL" and inspect.iscoroutinefunction(operation.function))
    ):
        raise ApprovalRequired()
    approved_request_id(caller, operation.name, value)


def approved_request_id(caller, operation_name, value):
    """Identify the unconsumed approved call whose transaction owns a database effect."""
    from app.infrastructure.operations.pipeline import ApprovalRequired

    if (
        caller.origin != "mcp"
        or caller.sessions is None
        or caller.user_id is None
        or caller.run_id is None
        or caller.conversation_id is None
    ):
        raise ApprovalRequired()
    with unit_of_work(caller.sessions, read_only=True) as session:
        row = storage.approved_call(
            session,
            caller.user_id,
            caller.run_id,
            caller.conversation_id,
            operation_name,
            _call_digest(value),
            caller.tool_call_id,
        )
        if row is None:
            raise ApprovalRequired()
        return row["id"]


def approved_preparation(caller, operation_name, value) -> tuple[UUID, object]:
    from app.infrastructure.operations.pipeline import ApprovalRequired

    prepared = _prepared_call.get()
    if (
        prepared is None
        or caller.origin != "mcp"
        or caller.sessions is not prepared.sessions
        or caller.user_id != prepared.user_id
        or caller.run_id != prepared.run_id
        or caller.conversation_id != prepared.conversation_id
        or caller.tool_call_id != prepared.tool_call_id
        or operation_name != prepared.operation
        or _call_digest(value) != prepared.digest
    ):
        raise ApprovalRequired()
    return prepared.approval_id, prepared.prepared


def execute_approved(caller, operation, value, invoke):
    """Commit one database effect with its event, or roll it back and retain failure."""
    from app.infrastructure.operations.pipeline import ApprovalRequired
    from app.library.handlers import CitationCheck, prepare_source, validate_prepared_source

    if (
        caller.origin != "mcp"
        or caller.sessions is None
        or caller.user_id is None
        or caller.run_id is None
        or caller.conversation_id is None
        or has_ambient_transaction()
    ):
        raise ApprovalRequired()
    digest = _call_digest(value)
    with unit_of_work(caller.sessions, read_only=True) as session:
        frozen = storage.approved_call(
            session,
            caller.user_id,
            caller.run_id,
            caller.conversation_id,
            operation.name,
            digest,
            caller.tool_call_id,
        )
        if frozen is None:
            raise ApprovalRequired()
    failure = None
    result = None
    evidence = []
    prepared = None
    try:
        for source in frozen["evidence"]:
            check = msgspec.convert({"handle": source["handle"]}, CitationCheck)
            evidence.append(prepare_source(caller, check.handle))
        if operation.prepare_approved is not None:
            prepared = operation.prepare_approved(caller, value)
    except Exception as error:
        failure = error
    with unit_of_work(_sessions(caller)) as session:
        storage.require_run(session, caller.user_id, caller.run_id, caller.conversation_id)
        row = storage.approved_call(
            session,
            caller.user_id,
            caller.run_id,
            caller.conversation_id,
            operation.name,
            digest,
            caller.tool_call_id,
        )
        if (
            row is None
            or row["id"] != frozen["id"]
            or row["evidence"] != frozen["evidence"]
            or _call_digest(value) != digest
        ):
            raise ApprovalRequired()
        try:
            with session.begin_nested(), approved_transaction(caller.sessions, session):
                if failure is not None:
                    raise failure
                for source in evidence:
                    validate_prepared_source(caller, session, source)
                token = _prepared_call.set(
                    _PreparedCall(
                        sessions=caller.sessions,
                        user_id=caller.user_id,
                        run_id=caller.run_id,
                        conversation_id=caller.conversation_id,
                        tool_call_id=caller.tool_call_id,
                        operation=operation.name,
                        approval_id=row["id"],
                        digest=digest,
                        prepared=prepared,
                    )
                )
                try:
                    result = invoke()
                    storage.append_event(session, row["id"], "executed", caller.user_id, None)
                finally:
                    _prepared_call.reset(token)
        except Exception as error:
            storage.append_event(session, row["id"], "failed", caller.user_id, None)
            failure = error
    if failure is not None:
        raise failure
    return result


def claim_external_approved(caller, operation, value) -> UUID:
    """Consume one approved external call before its single dispatch.

    An external effect cannot share a database transaction, so its executed event
    records the dispatch; the outcome is in the call log. A second claim fails.
    """
    from app.infrastructure.operations.pipeline import ApprovalRequired

    if (
        caller.origin != "mcp"
        or operation.effects != "EXTERNAL"
        or caller.sessions is None
        or caller.user_id is None
        or caller.run_id is None
        or caller.conversation_id is None
        or has_ambient_transaction()
    ):
        raise ApprovalRequired()
    with unit_of_work(_sessions(caller)) as session:
        storage.require_run(session, caller.user_id, caller.run_id, caller.conversation_id)
        row = storage.approved_call(
            session,
            caller.user_id,
            caller.run_id,
            caller.conversation_id,
            operation.name,
            _call_digest(value),
            caller.tool_call_id,
        )
        if row is None:
            raise ApprovalRequired()
        storage.append_event(session, row["id"], "executed", caller.user_id, None)
        return row["id"]


def claim_granted(caller, operation, value) -> UUID:
    """Allow a granted call when its notebook was granted in this conversation.

    The call log records each one; the grant's approval keeps its own single execution.
    """
    from app.infrastructure.operations.pipeline import ApprovalRequired

    path = getattr(value, operation.granted or "", None)
    if (
        caller.origin != "mcp"
        or operation.granted is None
        or not isinstance(path, str)
        or caller.sessions is None
        or caller.user_id is None
        or caller.run_id is None
        or caller.conversation_id is None
        or has_ambient_transaction()
    ):
        raise ApprovalRequired()
    with unit_of_work(_sessions(caller)) as session:
        storage.require_run(session, caller.user_id, caller.run_id, caller.conversation_id)
        approval = storage.grant_for(session, caller.user_id, caller.conversation_id, path)
    if approval is None:
        raise ApprovalRequired()
    return approval


def list_grants(caller, conversation):
    with unit_of_work(_sessions(caller), read_only=True) as session:
        return [
            {
                "id": row["id"],
                "conversation_id": row["conversation_id"],
                "path": row["path"],
                "approval_id": row["approval_id"],
                "created_at": row["created_at"].isoformat(),
            }
            for row in storage.list_grants(session, caller.user_id, conversation)
        ]


def revoke_grant(caller, identifier):
    with unit_of_work(_sessions(caller)) as session:
        if storage.revoke_grant(session, caller.user_id, identifier) != 1:
            raise NotFound()


def status_expression():
    """Return the existing append-only event status expression without reading data."""
    return storage.status_expression()


def reject_pending(session, user, run):
    for identifier in storage.pending(session, run):
        storage.append_event(session, identifier, "rejected", user, None)
