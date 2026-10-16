"""Run records and serialized conversation runtime state."""

from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    LargeBinary,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.database.metadata import metadata

agent_runs = Table(
    "agent_runs",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("conversation_id", UUID, ForeignKey("conversations.id"), nullable=False),
    Column("started_by", UUID, ForeignKey("users.id"), nullable=False),
    Column("status", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("instructions_version", Text, nullable=False),
    Column("token_sha256", LargeBinary, unique=True),
    Column("token_expires_at", DateTime(timezone=True)),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("ended_at", DateTime(timezone=True)),
    CheckConstraint(
        "status in ('running','paused','finished','failed','cancelled')", name="status"
    ),
)
agent_sessions = Table(
    "agent_sessions",
    metadata,
    Column("conversation_id", UUID, ForeignKey("conversations.id"), primary_key=True),
    Column("state", JSONB, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)
