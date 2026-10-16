"""Account records; transaction ownership belongs to services."""

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

from app.infrastructure.database.metadata import metadata

users = Table(
    "users",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("username", Text, nullable=False, unique=True),
    Column("display_name", Text, nullable=False),
    Column("disabled_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint("username = lower(username)", name="lower_username"),
)
local_credentials = Table(
    "local_credentials",
    metadata,
    Column("user_id", UUID, ForeignKey("users.id"), primary_key=True),
    Column("password_hash", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)
user_roles = Table(
    "user_roles",
    metadata,
    Column("user_id", UUID, ForeignKey("users.id"), primary_key=True),
    Column("role", Text, primary_key=True),
    Column("granted_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint("role in ('member','admin')", name="role"),
)
auth_sessions = Table(
    "auth_sessions",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("user_id", UUID, ForeignKey("users.id"), nullable=False, index=True),
    Column("token_sha256", LargeBinary, nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("idle_expires_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    Column("revoked_reason", Text),
)
