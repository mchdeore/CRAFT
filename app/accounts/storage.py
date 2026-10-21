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
workspace_tokens = Table(
    "workspace_tokens",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("user_id", UUID, ForeignKey("users.id"), nullable=False, index=True),
    Column("token_sha256", LargeBinary, nullable=False, unique=True),
    Column("label", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("last_used_at", DateTime(timezone=True)),
    Column("revoked_at", DateTime(timezone=True)),
    CheckConstraint("octet_length(token_sha256) = 32", name="token_sha256"),
)


def account(session, username: str, lock: bool = False):
    from sqlalchemy import select

    statement = select(users).where(users.c.username == username)
    if lock:
        statement = statement.with_for_update()
    return session.execute(statement).mappings().first()


def credentials(session, user):
    from sqlalchemy import select

    return session.execute(
        select(local_credentials.c.password_hash).where(local_credentials.c.user_id == user)
    ).scalar_one_or_none()


def roles_for(session, user):
    from sqlalchemy import select

    return list(
        session.execute(select(user_roles.c.role).where(user_roles.c.user_id == user)).scalars()
    )


def revoke_sessions(session, user, at, reason: str, only=None) -> None:
    statement = auth_sessions.update().where(
        auth_sessions.c.user_id == user, auth_sessions.c.revoked_at.is_(None)
    )
    if only is not None:
        statement = statement.where(auth_sessions.c.id == only)
    session.execute(statement.values(revoked_at=at, revoked_reason=reason))
