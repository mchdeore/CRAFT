"""Notification rows inside the caller's session: kinds and references only, never content."""

from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Table,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.orm import Session

from app.infrastructure.database.metadata import metadata

notifications = Table(
    "notifications",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("user_id", UUID, ForeignKey("users.id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("ref_kind", Text, nullable=False),
    Column("ref_id", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("read_at", DateTime(timezone=True)),
    CheckConstraint("kind IN ('job_done', 'job_failed', 'run_done', 'run_waiting')", name="kind"),
    CheckConstraint("ref_kind IN ('job', 'run')", name="ref_kind"),
)
Index("ix_notifications_user_id_id", notifications.c.user_id, notifications.c.id)


def add(session: Session, user, kind: str, ref_kind: str, ref_id: str) -> None:
    session.execute(
        notifications.insert().values(user_id=user, kind=kind, ref_kind=ref_kind, ref_id=ref_id)
    )


def page(session: Session, user, unread: bool, limit: int):
    """Newest first (IDs are time-ordered)."""
    query = select(notifications).where(notifications.c.user_id == user)
    if unread:
        query = query.where(notifications.c.read_at.is_(None))
    return list(session.execute(query.order_by(notifications.c.id.desc()).limit(limit)).mappings())


def mark_read(session: Session, user, identifier):
    """Mark one owned notification read (once); None when it isn't the user's."""
    session.execute(
        notifications.update()
        .where(
            notifications.c.id == identifier,
            notifications.c.user_id == user,
            notifications.c.read_at.is_(None),
        )
        .values(read_at=func.now())
    )
    return (
        session.execute(
            select(notifications).where(
                notifications.c.id == identifier, notifications.c.user_id == user
            )
        )
        .mappings()
        .first()
    )


def prune_read(session: Session, days: int) -> None:
    session.execute(
        notifications.delete().where(
            notifications.c.read_at < func.now() - func.make_interval(0, 0, 0, days)
        )
    )
