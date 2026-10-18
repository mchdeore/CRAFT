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
    case,
    cast,
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
    conversations = metadata.tables["conversations"]
    if (
        session.execute(
            select(conversations.c.id).where(
                conversations.c.id == conversation,
                conversations.c.owner_id == user,
                conversations.c.archived_at.is_(None),
            )
        ).first()
        is None
    ):
        from app.infrastructure.errors import NotFound

        raise NotFound()
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


def paused_state(session: Session, user, identifier, conversation):
    conversations = metadata.tables["conversations"]
    users = metadata.tables["users"]
    row = session.execute(
        select(agent_runs.c.status)
        .join(conversations, conversations.c.id == agent_runs.c.conversation_id)
        .join(users, users.c.id == agent_runs.c.started_by)
        .where(
            agent_runs.c.id == identifier,
            agent_runs.c.started_by == user,
            agent_runs.c.conversation_id == conversation,
            conversations.c.owner_id == user,
            conversations.c.archived_at.is_(None),
            users.c.disabled_at.is_(None),
        )
        .with_for_update(of=agent_runs)
    ).first()
    if row is None or row.status != "paused":
        raise Conflict()
    state = session.execute(
        select(agent_sessions.c.state).where(agent_sessions.c.conversation_id == conversation)
    ).scalar_one()
    return state


def resume(session: Session, user, identifier):
    session.execute(
        agent_runs.update()
        .where(agent_runs.c.id == identifier, agent_runs.c.started_by == user)
        .values(status="running", ended_at=None)
    )


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


def issue_token(session, user, identifier, digest, expires, context):
    row = session.execute(
        select(agent_runs.c.conversation_id)
        .where(
            agent_runs.c.id == identifier,
            agent_runs.c.started_by == user,
            agent_runs.c.status == "running",
        )
        .with_for_update()
    ).first()
    if row is None:
        from app.infrastructure.errors import NotFound

        raise NotFound()
    session.execute(
        agent_runs.update()
        .where(agent_runs.c.id == identifier)
        .values(token_sha256=digest, token_expires_at=expires)
    )
    state = (
        session.execute(
            select(agent_sessions.c.state)
            .where(agent_sessions.c.conversation_id == row.conversation_id)
            .with_for_update()
        ).scalar_one_or_none()
        or {}
    )
    state = state | {"craftTransport": context}
    statement = insert(agent_sessions).values(conversation_id=row.conversation_id, state=state)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[agent_sessions.c.conversation_id],
            set_={"state": state, "updated_at": func.now()},
        )
    )


def revoke_token(session, user, identifier):
    session.execute(
        agent_runs.update()
        .where(agent_runs.c.id == identifier, agent_runs.c.started_by == user)
        .values(token_sha256=None, token_expires_at=None)
    )


def principal(session, digest, at):
    users = metadata.tables["users"]
    conversations = metadata.tables["conversations"]
    roles = metadata.tables["user_roles"]
    row = (
        session.execute(
            select(agent_runs, agent_sessions.c.state)
            .join(users, users.c.id == agent_runs.c.started_by)
            .join(conversations, conversations.c.id == agent_runs.c.conversation_id)
            .outerjoin(agent_sessions, agent_sessions.c.conversation_id == conversations.c.id)
            .where(
                agent_runs.c.token_sha256 == digest,
                agent_runs.c.token_expires_at > at,
                agent_runs.c.status == "running",
                users.c.disabled_at.is_(None),
                conversations.c.archived_at.is_(None),
                conversations.c.owner_id == agent_runs.c.started_by,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return dict(row) | {
        "roles": list(
            session.execute(
                select(roles.c.role).where(roles.c.user_id == row["started_by"])
            ).scalars()
        )
    }


def recovery(session: Session, user, conversation):
    """Project owned metadata and verified linkage without loading native snapshots."""
    from app.approvals.handlers import approval_status_expression

    approval_requests = metadata.tables["approval_requests"]
    conversations = metadata.tables["conversations"]
    users = metadata.tables["users"]
    latest = (
        select(agent_runs.c.id)
        .where(agent_runs.c.conversation_id == conversation, agent_runs.c.started_by == user)
        .order_by(
            agent_runs.c.status.in_(("running", "paused")).desc(),
            agent_runs.c.started_at.desc(),
            agent_runs.c.id.desc(),
        )
        .limit(1)
        .correlate(None)
        .scalar_subquery()
    )
    native = agent_sessions.c.state
    state = native.op("->", return_type=JSONB)("state")
    pending = state.op("->", return_type=JSONB)("craftApprovals")
    single = pending.op("->", return_type=JSONB)(0)
    identifier = single.op("->>", return_type=Text)("id")
    interrupt = single.op("->>", return_type=Text)("interrupt")
    count = case(
        (func.jsonb_typeof(pending) == "array", func.jsonb_array_length(pending)),
        else_=None,
    )
    linked = (
        (func.jsonb_typeof(native) == "object")
        & (func.jsonb_typeof(state) == "object")
        & (count == 1)
        & (func.jsonb_typeof(single) == "object")
        & (func.jsonb_typeof(single.op("->", return_type=JSONB)("id")) == "string")
        & (func.jsonb_typeof(single.op("->", return_type=JSONB)("interrupt")) == "string")
        & (func.length(interrupt) > 0)
        & (cast(approval_requests.c.id, Text) == identifier)
        & (approval_requests.c.interrupt_id == cast(agent_runs.c.id, Text) + ":" + interrupt)
        & (approval_requests.c.run_id == agent_runs.c.id)
        & (approval_requests.c.conversation_id == conversations.c.id)
        & (approval_requests.c.requested_by == user)
    )
    return (
        session.execute(
            select(
                agent_runs.c.id.label("run_id"),
                agent_runs.c.status,
                agent_runs.c.started_at,
                agent_runs.c.ended_at,
                approval_requests.c.id.label("approval_id"),
                approval_status_expression().label("approval_status"),
            )
            .select_from(conversations)
            .join(users, users.c.id == conversations.c.owner_id)
            .outerjoin(agent_runs, agent_runs.c.id == latest)
            .outerjoin(
                agent_sessions,
                (agent_sessions.c.conversation_id == conversations.c.id)
                & (agent_runs.c.status == "paused"),
            )
            .outerjoin(approval_requests, linked)
            .where(
                conversations.c.id == conversation,
                conversations.c.owner_id == user,
                conversations.c.archived_at.is_(None),
                users.c.disabled_at.is_(None),
            )
        )
        .mappings()
        .first()
    )
