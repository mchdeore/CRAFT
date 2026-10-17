"""Short owned transactions for frozen actions and requester decisions."""

import hashlib
import inspect
import json

import msgspec

from app.approvals import storage
from app.infrastructure.access import require
from app.infrastructure.database.unit_of_work import approved_transaction, unit_of_work
from app.infrastructure.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.infrastructure.operations.pipeline import decode
from app.infrastructure.operations.registry import registry


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
        kind = "approved" if data.approve else "rejected"
        storage.append_event(session, data.id, kind, caller.user_id, data.note)
        return _summary(row, kind)


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
        or operation.effects not in {"DATA", "DOCUMENT"}
        or inspect.iscoroutinefunction(operation.function)
    ):
        raise ApprovalRequired()
    with unit_of_work(caller.sessions, read_only=True) as session:
        if (
            storage.approved_call(
                session,
                caller.user_id,
                caller.run_id,
                caller.conversation_id,
                operation.name,
                _call_digest(value),
                caller.tool_call_id,
            )
            is None
        ):
            raise ApprovalRequired()


def execute_approved(caller, operation, value, invoke):
    """Commit one database effect with its event, or roll it back and retain failure."""
    from app.infrastructure.operations.pipeline import ApprovalRequired

    failure = None
    result = None
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
        try:
            with session.begin_nested(), approved_transaction(caller.sessions, session):
                from app.library.handlers import CitationCheck, check_citation

                for evidence in row["evidence"]:
                    check = msgspec.convert({"handle": evidence["handle"]}, CitationCheck)
                    if check_citation(caller, check).status != "current":
                        raise Conflict()
                result = invoke()
                storage.append_event(session, row["id"], "executed", caller.user_id, None)
        except Exception as error:
            storage.append_event(session, row["id"], "failed", caller.user_id, None)
            failure = error
    if failure is not None:
        raise failure
    return result
