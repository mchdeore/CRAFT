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
notebook_grants = Table(
    "notebook_grants",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("approval_id", UUID, ForeignKey("approval_requests.id"), nullable=False, unique=True),
    Column("user_id", UUID, ForeignKey("users.id"), nullable=False),
    Column("conversation_id", UUID, ForeignKey("conversations.id"), nullable=False),
    Column("path", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("revoked_at", DateTime(timezone=True)),
)
Index(
    "uq_notebook_grants_active",
    notebook_grants.c.conversation_id,
    notebook_grants.c.path,
    unique=True,
    postgresql_where=text("revoked_at IS NULL"),
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


def pending(session, run):
    """The run's undecided requests."""
    from sqlalchemy import select

    undecided = select(approval_requests.c.id).where(
        approval_requests.c.run_id == run, status_expression() == "pending"
    )
    return list(session.execute(undecided).scalars())


def approved_call(session, user, run, conversation, operation, digest, tool_call_id=None):
    """Find an unconsumed frozen action for the authenticated active run."""
    from uuid import UUID

    from sqlalchemy import select

    runs = metadata.tables["agent_runs"]
    statement = owned_statement(user)
    if tool_call_id is not None:
        sessions = metadata.tables["agent_sessions"]
        state = session.execute(
            select(sessions.c.state).where(sessions.c.conversation_id == conversation)
        ).scalar_one_or_none()
        pending = (state or {}).get("state", {}).get("craftApprovals", [])
        matching = [item.get("id") for item in pending if item.get("callId") == tool_call_id]
        if len(matching) != 1:
            return None
        try:
            identifier = UUID(matching[0])
        except (TypeError, ValueError, AttributeError):
            return None
        statement = statement.where(approval_requests.c.id == identifier)
    rows = (
        session.execute(
            statement.where(
                approval_requests.c.run_id == run,
                approval_requests.c.conversation_id == conversation,
                approval_requests.c.operation == operation,
                approval_requests.c.arguments_sha256 == digest,
                runs.c.status == "running",
                status_expression() == "approved",
            ).limit(2)
        )
        .mappings()
        .all()
    )
    return rows[0] if len(rows) == 1 else None


def native_tool_call_id(session, row):
    """Read an unambiguous native call mapping after the request's ownership checks."""
    from sqlalchemy import select

    sessions = metadata.tables["agent_sessions"]
    stored = session.execute(
        select(sessions.c.state).where(sessions.c.conversation_id == row["conversation_id"])
    ).scalar_one_or_none()
    if not isinstance(stored, dict):
        return None
    state = stored.get("state")
    if not isinstance(state, dict):
        return None
    pending = state.get("craftApprovals")
    if not isinstance(pending, list):
        return None
    matching = [
        item
        for item in pending
        if isinstance(item, dict)
        and item.get("id") == str(row["id"])
        and isinstance(item.get("interrupt"), str)
        and row["interrupt_id"] == str(row["run_id"]) + ":" + item["interrupt"]
    ]
    if len(matching) != 1:
        return None
    identifier = matching[0].get("callId")
    return identifier if isinstance(identifier, str) and identifier else None


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


def add_grant(session, approval, user, conversation, path):
    from sqlalchemy.dialects.postgresql import insert

    session.execute(
        insert(notebook_grants)
        .values(approval_id=approval, user_id=user, conversation_id=conversation, path=path)
        .on_conflict_do_nothing()
    )


def grant_for(session, user, conversation, path):
    from sqlalchemy import select

    conversations = metadata.tables["conversations"]
    return session.execute(
        select(notebook_grants.c.approval_id)
        .join(conversations, conversations.c.id == notebook_grants.c.conversation_id)
        .where(
            notebook_grants.c.user_id == user,
            notebook_grants.c.conversation_id == conversation,
            notebook_grants.c.path == path,
            notebook_grants.c.revoked_at.is_(None),
            conversations.c.owner_id == user,
        )
    ).scalar_one_or_none()


def list_grants(session, user, conversation=None):
    from sqlalchemy import select

    statement = select(notebook_grants).where(
        notebook_grants.c.user_id == user, notebook_grants.c.revoked_at.is_(None)
    )
    if conversation is not None:
        statement = statement.where(notebook_grants.c.conversation_id == conversation)
    return (
        session.execute(statement.order_by(notebook_grants.c.created_at.desc()).limit(100))
        .mappings()
        .all()
    )


def revoke_grant(session, user, identifier):
    from sqlalchemy import func

    return session.execute(
        notebook_grants.update()
        .where(
            notebook_grants.c.id == identifier,
            notebook_grants.c.user_id == user,
            notebook_grants.c.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    ).rowcount
