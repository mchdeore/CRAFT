"""Append-only approval requests and events; the status view is read-only."""

from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.database.metadata import metadata

approval_requests = Table(
    "approval_requests",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("run_id", UUID, ForeignKey("agent_runs.id"), nullable=False),
    Column("conversation_id", UUID, ForeignKey("conversations.id"), nullable=False),
    Column("requested_by", UUID, ForeignKey("users.id"), nullable=False),
    Column("operation", Text, nullable=False),
    Column("arguments", JSONB, nullable=False),
    Column("arguments_sha256", LargeBinary, nullable=False),
    Column("evidence", JSONB, nullable=False),
    Column("reasoning", Text, nullable=False),
    Column("retrieval_path", JSONB, nullable=False),
    Column("interrupt_id", Text, nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)
approval_events = Table(
    "approval_events",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("request_id", UUID, ForeignKey("approval_requests.id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("actor_id", UUID, ForeignKey("users.id")),
    Column("at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("note", Text),
    CheckConstraint("kind in ('requested','approved','rejected','executed','failed')", name="kind"),
)
Index(
    "uq_approval_events_decision",
    approval_events.c.request_id,
    unique=True,
    postgresql_where=text("kind in ('approved','rejected')"),
)
Index(
    "uq_approval_events_execution",
    approval_events.c.request_id,
    unique=True,
    postgresql_where=text("kind in ('executed','failed')"),
)
APPROVAL_STATUS = text("SELECT request_id, kind, actor_id, at FROM approval_status")
