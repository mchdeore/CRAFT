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
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Session

from app.infrastructure.database.metadata import metadata
from app.infrastructure.errors import Conflict, RateLimited

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


def start(session: Session, user, conversation, identifier, model, version):
    session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": "run-account:" + str(user)},
    )
    if session.execute(select(agent_runs.c.id).where(agent_runs.c.id == identifier)).first():
        raise Conflict()
    active = agent_runs.c.status.in_(["running", "paused"])
    if session.execute(
        select(agent_runs.c.id).where(
            agent_runs.c.conversation_id == conversation,
            active,
        )
    ).first():
        raise Conflict()
    count = session.execute(
        select(func.count())
        .select_from(agent_runs)
        .where(
            agent_runs.c.started_by == user,
            active,
        )
    ).scalar_one()
    if count >= 3:
        raise RateLimited()
    session.execute(
        agent_runs.insert().values(
            id=identifier,
            started_by=user,
            conversation_id=conversation,
            status="running",
            model=model,
            instructions_version=version,
        )
    )
    return session.execute(
        select(agent_sessions.c.state).where(
            agent_sessions.c.conversation_id == conversation,
        )
    ).scalar_one_or_none()


def finish(session: Session, user, identifier, conversation, status, state):
    row = session.execute(
        select(agent_runs.c.id)
        .where(
            agent_runs.c.id == identifier,
            agent_runs.c.started_by == user,
            agent_runs.c.status == "running",
        )
        .with_for_update()
    ).first()
    if row is None:
        return
    statement = insert(agent_sessions).values(conversation_id=conversation, state=state)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[agent_sessions.c.conversation_id],
            set_={"state": state, "updated_at": func.now()},
        )
    )
    session.execute(
        agent_runs.update()
        .where(agent_runs.c.id == identifier)
        .values(
            status=status,
            ended_at=func.now(),
            token_sha256=None,
            token_expires_at=None,
        )
    )
