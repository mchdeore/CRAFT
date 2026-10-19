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
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

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
    CheckConstraint("role in ('user','assistant','tool','activity','reasoning')", name="role"),
    UniqueConstraint("conversation_id", "seq"),
)


def history(session: Session, identifier):
    return list(
        session.execute(
            select(messages.c.content)
            .where(messages.c.conversation_id == identifier)
            .order_by(messages.c.seq)
        ).scalars()
    )


def append(session: Session, identifier, run, values):
    session.execute(
        select(conversations.c.id).where(conversations.c.id == identifier).with_for_update()
    )
    seq = session.execute(
        select(func.coalesce(func.max(messages.c.seq), 0)).where(
            messages.c.conversation_id == identifier
        )
    ).scalar_one()
    for value in values:
        seq += 1
        session.execute(
            messages.insert().values(
                conversation_id=identifier,
                run_id=run,
                seq=seq,
                role=value["role"],
                content=value,
            )
        )
    session.execute(
        conversations.update().where(conversations.c.id == identifier).values(updated_at=func.now())
    )


def summaries(session, user):
    return (
        session.execute(
            select(conversations)
            .where(conversations.c.owner_id == user)
            .order_by(conversations.c.updated_at.desc(), conversations.c.id.desc())
            .limit(100)
        )
        .mappings()
        .all()
    )


def create(session, user, title):
    return (
        session.execute(
            conversations.insert().values(owner_id=user, title=title).returning(conversations)
        )
        .mappings()
        .one()
    )


def read_owned(session, user, identifier, *, active=True, lock=False):
    query = select(conversations).where(
        conversations.c.owner_id == user, conversations.c.id == identifier
    )
    if active:
        query = query.where(conversations.c.archived_at.is_(None))
    if lock:
        query = query.with_for_update()
    return session.execute(query).mappings().first()


def serialise_runs(session, user):
    session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": "run-account:" + str(user)},
    )


def active_run(session, identifier):
    runs = metadata.tables["agent_runs"]
    return session.execute(
        select(runs.c.id).where(
            runs.c.conversation_id == identifier, runs.c.status.in_(["running", "paused"])
        )
    ).first()


def update_conversation(session, identifier, values):
    return (
        session.execute(
            conversations.update()
            .where(conversations.c.id == identifier)
            .values(**values)
            .returning(conversations)
        )
        .mappings()
        .one()
    )


def file_rows(session, identifier):
    table = metadata.tables["workspace_files"]
    return (
        session.execute(
            select(table)
            .where(table.c.conversation_id == identifier)
            .order_by(table.c.created_at.desc(), table.c.id.desc())
        )
        .mappings()
        .all()
    )


def file_count(session, identifier):
    table = metadata.tables["workspace_files"]
    return session.execute(
        select(func.count()).select_from(table).where(table.c.conversation_id == identifier)
    ).scalar_one()


def insert_file(session, values):
    table = metadata.tables["workspace_files"]
    return session.execute(table.insert().values(**values).returning(table.c.id)).scalar_one()


def file_state(session, identifier, state):
    table = metadata.tables["workspace_files"]
    return (
        session.execute(
            table.update().where(table.c.id == identifier).values(state=state).returning(table)
        )
        .mappings()
        .first()
    )


def read_file(session, conversation, identifier):
    table = metadata.tables["workspace_files"]
    return (
        session.execute(
            select(table).where(table.c.id == identifier, table.c.conversation_id == conversation)
        )
        .mappings()
        .first()
    )


def remove_file(session, conversation, identifier):
    table = metadata.tables["workspace_files"]
    return session.execute(
        table.delete()
        .where(table.c.id == identifier, table.c.conversation_id == conversation)
        .returning(table.c.id)
    ).first()


def clear_files(session, conversation):
    table = metadata.tables["workspace_files"]
    session.execute(table.delete().where(table.c.conversation_id == conversation))
