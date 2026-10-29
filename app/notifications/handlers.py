"""Notifications: a person's finished jobs and task-mode runs. Clients poll; no email or push."""

from typing import Annotated, Literal
from uuid import UUID

import msgspec

from app.infrastructure.access import PUBLIC, SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape
from app.notifications import service

NotificationKind = Literal["job_done", "job_failed", "run_done", "run_waiting"]


class NotificationPageRequest(Shape):
    unread: bool = False
    limit: Annotated[int, msgspec.Meta(ge=1, le=100)] = 50


class NotificationRef(Shape):
    id: UUID


class Notification(Shape):
    """ref_kind and ref_id name what it is about: a job (GET /jobs/{jobId}) or a run."""

    id: UUID
    kind: NotificationKind
    ref_kind: Literal["job", "run"]
    ref_id: str
    created_at: str
    read_at: str | None


@operation(SIGNED_IN, get="/notifications")
def list_notifications(caller: Caller, request: NotificationPageRequest) -> list[Notification]:
    """Your newest notifications, or only the unread ones."""
    rows = service.page(caller, request.unread, request.limit)
    return [Notification(**row) for row in rows]


@operation(SIGNED_IN, post="/notifications/{id}/read")
def read_notification(caller: Caller, request: NotificationRef) -> Notification:
    """Mark one of your notifications read."""
    return Notification(**service.mark_read(caller, request.id))


class JobEnded(Shape):
    user: UUID
    job_id: int
    failed: bool


@operation(PUBLIC)
def job_ended(caller: Caller, request: JobEnded) -> None:
    """Tell a person that a job they queued finished or finally failed; a Python-only entry."""
    service.job_ended(caller, request.user, request.job_id, request.failed)


def notify(session, user, kind: str, ref_kind: str, ref_id: str) -> None:
    """Write a notification in the calling feature's transaction."""
    service.add(session, user, kind, ref_kind, ref_id)


def prune_read(session, days: int) -> None:
    """Delete notifications read more than this many days ago."""
    service.prune_read(session, days)
