"""Append-only call metadata. This table never holds request or result content."""

from typing import Any

from sqlalchemy import (
    UUID,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    Table,
    Text,
    text,
)
from sqlalchemy.orm import Session

from app.infrastructure.database.metadata import metadata

call_log = Table(
    "call_log",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("kind", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("origin", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("error_code", Text),
    Column("duration_ms", Integer, nullable=False),
    Column("db_statements", Integer, nullable=False, server_default=text("0")),
    Column("db_ms", Integer, nullable=False, server_default=text("0")),
    Column("request_id", UUID, nullable=False),
    Column("call_id", UUID, nullable=False),
    Column("parent_call_id", UUID),
    Column("user_id", UUID),
    Column("auth_session_id", UUID),
    Column("conversation_id", UUID),
    Column("run_id", UUID),
    Column("tool_call_id", Text),
    CheckConstraint("kind in ('operation','step')", name="kind"),
    CheckConstraint("origin in ('http','mcp','python')", name="origin"),
    CheckConstraint("outcome in ('ok','error')", name="outcome"),
)
Index("ix_call_log_at", call_log.c.at)
Index("ix_call_log_user_id_at", call_log.c.user_id, call_log.c.at)
Index("ix_call_log_run_id", call_log.c.run_id)
Index("ix_call_log_request_id", call_log.c.request_id)


def append_call(session: Session, fields: dict[str, Any]) -> None:
    """Append only declared metadata using the orchestration's explicit session."""
    session.execute(
        call_log.insert().values(
            **{
                name: fields[name]
                for name in call_log.c.keys()
                if name in fields and name not in {"id", "at"}
            }
        )
    )
