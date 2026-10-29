"""A person's notifications: written when their job or task-mode run ends or waits."""

from typing import Any

from app.infrastructure.access import Caller
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import NotFound
from app.notifications import storage


def shape(row) -> dict[str, Any]:
    """A notification row as the Notification shape's fields."""
    return {
        "id": row["id"],
        "kind": row["kind"],
        "ref_kind": row["ref_kind"],
        "ref_id": row["ref_id"],
        "created_at": row["created_at"].isoformat(),
        "read_at": row["read_at"].isoformat() if row["read_at"] else None,
    }


def page(caller: Caller, unread: bool, limit: int) -> list[dict[str, Any]]:
    """The caller's newest notifications, or only the unread ones."""
    assert caller.sessions is not None
    with unit_of_work(caller.sessions, read_only=True) as session:
        return [shape(row) for row in storage.page(session, caller.user_id, unread, limit)]


def mark_read(caller: Caller, identifier) -> dict[str, Any]:
    """Mark one of the caller's notifications read; another person's is not found."""
    assert caller.sessions is not None
    with unit_of_work(caller.sessions) as session:
        row = storage.mark_read(session, caller.user_id, identifier)
        if row is None:
            raise NotFound()
        return shape(row)


def add(session, user, kind: str, ref_kind: str, ref_id: str) -> None:
    """Write a notification in the caller's transaction (a job's second one is dropped)."""
    storage.add(session, user, kind, ref_kind, ref_id)


def job_ended(caller: Caller, user, job_id: int, failed: bool) -> None:
    """Tell a person their job finished or finally failed, in its own transaction."""
    assert caller.sessions is not None
    with unit_of_work(caller.sessions) as session:
        storage.add(session, user, "job_failed" if failed else "job_done", "job", str(job_id))


def prune_read(session, days: int) -> None:
    """Delete notifications read more than this many days ago."""
    storage.prune_read(session, days)
