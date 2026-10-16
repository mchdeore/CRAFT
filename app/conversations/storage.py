"""Conversation and append-only message records."""

from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.database.metadata import metadata

conversations = Table(
    "conversations",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("owner_id", UUID, ForeignKey("users.id"), nullable=False),
    Column("title", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("archived_at", DateTime(timezone=True)),
)
Index(
    "ix_conversations_owner_id_updated_at",
    conversations.c.owner_id,
    conversations.c.updated_at.desc(),
)
messages = Table(
    "messages",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("conversation_id", UUID, ForeignKey("conversations.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("role", Text, nullable=False),
    Column("content", JSONB, nullable=False),
    Column(
        "run_id",
        UUID,
        ForeignKey("agent_runs.id", use_alter=True, name="fk_messages_run_id_agent_runs"),
    ),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint("role in ('user','assistant','tool','activity')", name="role"),
    UniqueConstraint("conversation_id", "seq"),
)
