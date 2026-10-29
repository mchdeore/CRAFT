"""A person's notifications: written when their job or task-mode run ends or waits."""

from typing import Any

from app.infrastructure.access import Caller
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import NotFound
from app.notifications import storage


def shape(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "ref_kind": row["ref_kind"],
        "ref_id": row["ref_id"],
        "created_at": row["created_at"].isoformat(),
        "read_at": row["read_at"].isoformat() if row["read_at"] else None,
    }


def page(caller: Caller, unread: bool, limit: int) -> list[dict[str, Any]]:
    assert caller.sessions is not None
    with unit_of_work(caller.sessions, read_only=True) as session:
        return [shape(row) for row in storage.page(session, caller.user_id, unread, limit)]


def mark_read(caller: Caller, identifier) -> dict[str, Any]:
    assert caller.sessions is not None
    with unit_of_work(caller.sessions) as session:
        row = storage.mark_read(session, caller.user_id, identifier)
        if row is None:
            raise NotFound()
        return shape(row)


def add(session, user, kind: str, ref_kind: str, ref_id: str) -> None:
    storage.add(session, user, kind, ref_kind, ref_id)


def job_ended(sessions, user, job_id: int, failed: bool) -> None:
    with unit_of_work(sessions) as session:
        storage.add(session, user, "job_failed" if failed else "job_done", "job", str(job_id))


def prune_read(session, days: int) -> None:
    storage.prune_read(session, days)
