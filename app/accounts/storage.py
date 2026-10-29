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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.database.metadata import metadata

users = Table(
    "users",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("username", Text, nullable=False, unique=True),
    Column("display_name", Text, nullable=False),
    Column("disabled_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("language", Text, nullable=False, server_default=text("'en'")),
    Column("idp_tenant_id", UUID),
    Column("idp_object_id", UUID),
    Column("email", Text, unique=True),
    CheckConstraint("username = lower(username)", name="lower_username"),
    CheckConstraint("language IN ('en', 'fr')", name="language"),
    CheckConstraint("email = lower(email)", name="lower_email"),
    CheckConstraint("(idp_tenant_id IS NULL) = (idp_object_id IS NULL)", name="idp_key"),
    UniqueConstraint("idp_tenant_id", "idp_object_id"),
)
access_requests = Table(
    "access_requests",
    metadata,
    Column("tenant_id", UUID, primary_key=True),
    Column("object_id", UUID, primary_key=True),
    Column("email", Text),
    Column("name", Text, nullable=False),
    Column("first_seen_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)
sign_in_flows = Table(
    "sign_in_flows",
    metadata,
    Column("id_sha256", LargeBinary, primary_key=True),
    Column("flow", JSONB, nullable=False),
    Column("return_to", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("octet_length(id_sha256) = 32", name="id_sha256"),
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


def set_language(session, user, language: str) -> None:
    session.execute(users.update().where(users.c.id == user).values(language=language))


def language(session, user) -> str | None:
    from sqlalchemy import select

    return session.execute(select(users.c.language).where(users.c.id == user)).scalar_one_or_none()


def linked(session, tenant, oid, email: str | None):
    """The user with this provider key; else link an unlinked user with this email to it."""
    from sqlalchemy import select, update

    row = (
        session.execute(
            select(users)
            .where(users.c.idp_tenant_id == tenant, users.c.idp_object_id == oid)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is not None or email is None:
        return row
    # Microsoft's stable key is (tid, oid); the email only finds the person the first time.
    return (
        session.execute(
            update(users)
            .where(users.c.email == email, users.c.idp_object_id.is_(None))
            .values(idp_tenant_id=tenant, idp_object_id=oid)
            .returning(users)
        )
        .mappings()
        .first()
    )


def by_email(session, email: str):
    from sqlalchemy import select

    return session.execute(select(users).where(users.c.email == email)).mappings().first()


def take_request(session, email: str):
    """Remove and return the newest access request with this email."""
    from sqlalchemy import select

    row = (
        session.execute(
            select(access_requests)
            .where(access_requests.c.email == email)
            .order_by(access_requests.c.last_seen_at.desc())
            .limit(1)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is not None:
        session.execute(
            access_requests.delete().where(
                access_requests.c.tenant_id == row["tenant_id"],
                access_requests.c.object_id == row["object_id"],
            )
        )
    return row


def request_access(session, tenant, oid, email: str | None, name: str) -> None:
    from sqlalchemy.dialects.postgresql import insert

    statement = insert(access_requests).values(
        tenant_id=tenant, object_id=oid, email=email, name=name
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["tenant_id", "object_id"],
            set_={"email": email, "name": name, "last_seen_at": text("now()")},
        )
    )


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


def prune_expired(session, at) -> None:
    """Sign-in sessions past their absolute expiry and expired workspace tokens."""
    session.execute(auth_sessions.delete().where(auth_sessions.c.expires_at < at))
    session.execute(workspace_tokens.delete().where(workspace_tokens.c.expires_at < at))
