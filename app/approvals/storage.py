"""Append-only approval requests and events; the status view is read-only."""

from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.database.metadata import metadata

approval_requests = Table(
    "approval_requests",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("run_id", UUID, ForeignKey("agent_runs.id"), nullable=False),
    Column("conversation_id", UUID, ForeignKey("conversations.id"), nullable=False),
    Column("requested_by", UUID, ForeignKey("users.id"), nullable=False),
    Column("operation", Text, nullable=False),
    Column("arguments", JSONB, nullable=False),
    Column("arguments_sha256", LargeBinary, nullable=False),
    Column("evidence", JSONB, nullable=False),
    Column("reasoning", Text, nullable=False),
    Column("retrieval_path", JSONB, nullable=False),
    Column("interrupt_id", Text, nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)
approval_events = Table(
    "approval_events",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("request_id", UUID, ForeignKey("approval_requests.id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("actor_id", UUID, ForeignKey("users.id")),
    Column("at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("note", Text),
    CheckConstraint("kind in ('requested','approved','rejected','executed','failed')", name="kind"),
)
Index(
    "uq_approval_events_decision",
    approval_events.c.request_id,
    unique=True,
    postgresql_where=text("kind in ('approved','rejected')"),
)
Index(
    "uq_approval_events_execution",
    approval_events.c.request_id,
    unique=True,
    postgresql_where=text("kind in ('executed','failed')"),
)
APPROVAL_STATUS = text("SELECT request_id, kind, actor_id, at FROM approval_status")


def status_expression():
    from sqlalchemy import case, func, select

    latest = (
        select(approval_events.c.kind)
        .where(approval_events.c.request_id == approval_requests.c.id)
        .order_by(approval_events.c.at.desc(), approval_events.c.id.desc())
        .limit(1)
        .correlate(approval_requests)
        .scalar_subquery()
    )
    return case((latest == "requested", "pending"), else_=func.coalesce(latest, "pending"))


def owned_statement(user):
    from sqlalchemy import select

    conversations = metadata.tables["conversations"]
    runs = metadata.tables["agent_runs"]
    return (
        select(approval_requests, status_expression().label("status"))
        .join(conversations, conversations.c.id == approval_requests.c.conversation_id)
        .join(runs, runs.c.id == approval_requests.c.run_id)
        .where(
            approval_requests.c.requested_by == user,
            conversations.c.owner_id == user,
            runs.c.started_by == user,
            runs.c.conversation_id == approval_requests.c.conversation_id,
        )
    )


def require_run(session, user, run, conversation):
    from sqlalchemy import select

    from app.infrastructure.errors import NotFound

    runs = metadata.tables["agent_runs"]
    conversations = metadata.tables["conversations"]
    users = metadata.tables["users"]
    row = session.execute(
        select(runs.c.id)
        .join(conversations, conversations.c.id == runs.c.conversation_id)
        .join(users, users.c.id == runs.c.started_by)
        .where(
            runs.c.id == run,
            runs.c.conversation_id == conversation,
            runs.c.started_by == user,
            runs.c.status.in_(["running", "paused"]),
            conversations.c.owner_id == user,
            conversations.c.archived_at.is_(None),
            users.c.disabled_at.is_(None),
        )
        .with_for_update(of=runs)
    ).first()
    if row is None:
        raise NotFound()


def by_interrupt(session, interrupt):
    from sqlalchemy import select

    return (
        session.execute(
            select(approval_requests).where(approval_requests.c.interrupt_id == interrupt)
        )
        .mappings()
        .first()
    )


def append_request(session, user, data, arguments, digest, interrupt):
    return (
        session.execute(
            approval_requests.insert()
            .values(
                run_id=data.run_id,
                conversation_id=data.conversation_id,
                requested_by=user,
                operation=data.operation,
                arguments=arguments,
                arguments_sha256=digest,
                evidence=data.evidence,
                reasoning=data.reasoning,
                retrieval_path=data.retrieval_path,
                interrupt_id=interrupt,
            )
            .returning(approval_requests)
        )
        .mappings()
        .one()
    )


def append_event(session, identifier, kind, user, note):
    session.execute(
        approval_events.insert().values(request_id=identifier, kind=kind, actor_id=user, note=note)
    )


def owned(session, user, identifier, *, lock=False):
    from app.infrastructure.errors import NotFound

    statement = owned_statement(user).where(approval_requests.c.id == identifier)
    if lock:
        statement = statement.with_for_update(of=approval_requests)
    row = session.execute(statement).mappings().first()
    if row is None:
        raise NotFound()
    return row


def list_owned(session, user, status=None):
    statement = owned_statement(user)
    if status is not None:
        statement = statement.where(status_expression() == status)
    return (
        session.execute(
            statement.order_by(
                approval_requests.c.created_at.desc(), approval_requests.c.id.desc()
            ).limit(100)
        )
        .mappings()
        .all()
    )


def status(session, identifier):
    from sqlalchemy import select

    return session.execute(
        select(status_expression())
        .select_from(approval_requests)
        .where(approval_requests.c.id == identifier)
    ).scalar_one()


def events(session, identifier):
    from sqlalchemy import select

    return (
        session.execute(
            select(approval_events)
            .where(approval_events.c.request_id == identifier)
            .order_by(approval_events.c.at, approval_events.c.id)
        )
        .mappings()
        .all()
    )
