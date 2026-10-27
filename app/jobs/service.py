"""Defer, read, retry and cancel jobs; a caller sees their own jobs, managers see all."""

from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure import queue
from app.infrastructure.access import PERMISSIONS, Caller
from app.infrastructure.config.settings import QUEUES
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import Conflict, NotFound
from app.jobs import storage


def manager(caller: Caller) -> bool:
    return bool(caller.roles & PERMISSIONS["library.manage"])


def defer(session: Session, kind: str, args: dict, requested_by: Any = None) -> int:
    """Queue one job in the caller's transaction; a held lock key returns the active job."""
    declared = queue.find(kind)
    if declared is None:
        raise NotFound()
    lock = (declared.lock_key(args) or None) if declared.lock_key else None
    if lock is not None and (existing := storage.active(session, lock)) is not None:
        return existing
    values = args | ({"requested_by": str(requested_by)} if requested_by is not None else {})
    return storage.defer(session, declared.queue, kind, values, lock)


def active(session: Session, lock: str) -> int | None:
    return storage.active(session, lock)


def state(session: Session, job_id: int) -> str | None:
    row = storage.read(session, job_id)
    return row["state"] if row else None


def cancel_by_id(session: Session, job_id: int) -> bool:
    return storage.cancel(session, job_id)


def shape(row) -> dict[str, Any]:
    result = {
        "job_id": str(row["job_id"]),
        "kind": row["kind"],
        "queue": row["queue"],
        "status": row["state"],
        "attempts": row["attempts"],
        "requested_by": row["requested_by"],
        "error": row["error"],
    }
    for key in ("enqueued_at", "started_at", "finished_at"):
        result[key] = row[key].isoformat() if row[key] else None
    return result


def _owned(session: Session, caller: Caller, job_id: int):
    row = storage.read(session, job_id)
    if row is None or not (manager(caller) or row["requested_by"] == str(caller.user_id)):
        raise NotFound()
    return row


def read(caller: Caller, job_id: int) -> dict[str, Any]:
    assert caller.sessions is not None
    with unit_of_work(caller.sessions, read_only=True) as session:
        return shape(_owned(session, caller, job_id))


def page(caller: Caller, state: str | None, offset: int, limit: int) -> dict[str, Any]:
    assert caller.sessions is not None
    owner = None if manager(caller) else str(caller.user_id)
    with unit_of_work(caller.sessions, read_only=True) as session:
        rows = storage.page(session, owner, state, offset, limit)
    return {
        "jobs": [shape(row) for row in rows[:limit]],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


def retry(caller: Caller, job_id: int) -> dict[str, Any]:
    """A failed, canceled or stopped job runs again as a new job with the same arguments."""
    assert caller.sessions is not None
    with unit_of_work(caller.sessions) as session:
        row = _owned(session, caller, job_id)
        declared = queue.find(row["kind"])
        if declared is None or not declared.retry:
            raise Conflict()
        if row["state"] not in {"failed", "canceled", "stopped"}:
            raise Conflict()
        args = {key: value for key, value in row["args"].items() if key != "requested_by"}
        new = defer(session, row["kind"], args, row["requested_by"])
        return shape(storage.read(session, new))


def cancel(caller: Caller, job_id: int) -> dict[str, Any]:
    assert caller.sessions is not None
    with unit_of_work(caller.sessions) as session:
        _owned(session, caller, job_id)
        if not storage.cancel(session, job_id):
            raise Conflict()
        return shape(storage.read(session, job_id))


def health(sessions) -> dict[str, Any]:
    with unit_of_work(sessions, read_only=True) as session:
        rows = storage.counts(session)
    queues = {
        name: {"queue": name, "oldest_queued_seconds": None} | dict.fromkeys(STATES, 0)
        for name in QUEUES
    }
    for row in rows:
        entry = queues.setdefault(
            row["queue"],
            {"queue": row["queue"], "oldest_queued_seconds": None} | dict.fromkeys(STATES, 0),
        )
        entry[row["state"]] = row["jobs"]
        if row["state"] == "queued" and row["oldest"] is not None:
            entry["oldest_queued_seconds"] = round(float(row["oldest"]), 1)
    return {"queues": list(queues.values()), "dead": sum(q["failed"] for q in queues.values())}


STATES = ("queued", "scheduled", "started", "stopping", "finished", "failed", "canceled", "stopped")
