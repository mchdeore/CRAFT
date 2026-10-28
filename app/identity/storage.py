"""craft-id's own records; transaction ownership belongs to services."""

from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    LargeBinary,
    Table,
    Text,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY

from app.infrastructure.database.metadata import metadata

id_accounts = Table(
    "id_accounts",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("gen_random_uuid()")),  # the oid
    Column("username", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("email", Text),
    Column("disabled_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint("username = lower(username)", name="lower_username"),
)
id_credentials = Table(
    "id_credentials",
    metadata,
    Column("account_id", UUID, ForeignKey("id_accounts.id"), primary_key=True),
    Column("password_hash", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)
id_clients = Table(
    "id_clients",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("gen_random_uuid()")),  # client ID
    Column("name", Text, nullable=False),
    Column("secret_sha256", LargeBinary, nullable=False),
    Column("redirect_uris", ARRAY(Text), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint("octet_length(secret_sha256) = 32", name="secret_sha256"),
)
id_codes = Table(
    "id_codes",
    metadata,
    Column("code_sha256", LargeBinary, primary_key=True),
    Column("client_id", UUID, ForeignKey("id_clients.id"), nullable=False),
    Column("account_id", UUID, ForeignKey("id_accounts.id"), nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("nonce", Text),
    Column("code_challenge", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True)),
    CheckConstraint("octet_length(code_sha256) = 32", name="code_sha256"),
)


def account(session, username: str):
    return (
        session.execute(select(id_accounts).where(id_accounts.c.username == username))
        .mappings()
        .first()
    )


def account_by_id(session, identifier):
    return (
        session.execute(select(id_accounts).where(id_accounts.c.id == identifier))
        .mappings()
        .first()
    )


def password_hash(session, identifier) -> str | None:
    return session.execute(
        select(id_credentials.c.password_hash).where(id_credentials.c.account_id == identifier)
    ).scalar_one_or_none()


def client(session, identifier):
    return (
        session.execute(select(id_clients).where(id_clients.c.id == identifier)).mappings().first()
    )
