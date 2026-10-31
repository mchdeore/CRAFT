"""Run records and serialized conversation runtime state."""

from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
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
    Column("tool_calls", Integer),
    Column("active_seconds", Float),
    Column("end_reason", Text),
    # A task-mode run has the longer limits and notifies its owner when it ends or waits.
    Column("mode", Text, nullable=False, server_default=text("'chat'")),
    CheckConstraint(
        "status in ('running','paused','finished','failed','cancelled')", name="status"
    ),
    CheckConstraint("mode IN ('chat', 'task')", name="mode"),
    CheckConstraint(
        "end_reason IS NULL OR end_reason IN ("
        "'finished', 'paused', 'time_limit', 'tool_limit', 'failed', 'cancelled')",
        name="end_reason",
    ),
    CheckConstraint("tool_calls IS NULL OR tool_calls >= 0", name="tool_calls"),
    CheckConstraint("active_seconds IS NULL OR active_seconds >= 0", name="active_seconds"),
)
# A conversation's running or paused run: starts, archiving and pending counts look it up.
Index(
    "ix_agent_runs_conversation_id_active",
    agent_runs.c.conversation_id,
    postgresql_where=text("status IN ('running', 'paused')"),
)
Index(
    "ix_agent_runs_conversation_id_started_at",
    agent_runs.c.conversation_id,
    agent_runs.c.started_at.desc(),
)
agent_sessions = Table(
    "agent_sessions",
    metadata,
    Column("conversation_id", UUID, ForeignKey("conversations.id"), primary_key=True),
    Column("state", JSONB, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)

# A run's segments (its start, then each resume) and the events they streamed, for re-attach.
agent_run_inputs = Table(
    "agent_run_inputs",
    metadata,
    Column("run_id", UUID, primary_key=True),
    Column("segment", Integer, primary_key=True),
    Column("owner_id", UUID, ForeignKey("users.id"), nullable=False),
    Column("input", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)
agent_run_events = Table(
    "agent_run_events",
    metadata,
    Column("run_id", UUID, primary_key=True),
    Column("segment", Integer, primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("event", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)
EVENTS_CHANNEL = "craft_agent_run_events"


def add_segment(session: Session, user, run, conversation, value) -> int:
    """Serialize on the run and record its next input; a run with another owner is refused."""
    conversations = metadata.tables["conversations"]
    owned = select(conversations.c.id).where(
        conversations.c.id == conversation,
        conversations.c.owner_id == user,
        conversations.c.archived_at.is_(None),
    )
    if session.execute(owned).first() is None:
        from app.infrastructure.errors import NotFound

        raise NotFound()
    session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": "run:" + str(run)}
    )
    owners = set(
        session.execute(
            select(agent_run_inputs.c.owner_id).where(agent_run_inputs.c.run_id == run)
        ).scalars()
    ) | set(
        session.execute(select(agent_runs.c.started_by).where(agent_runs.c.id == run)).scalars()
    )
    if owners - {user}:
        raise Conflict()
    segment = (
        session.execute(
            select(func.coalesce(func.max(agent_run_inputs.c.segment), 0)).where(
                agent_run_inputs.c.run_id == run
            )
        ).scalar_one()
        + 1
    )
    session.execute(
        agent_run_inputs.insert().values(run_id=run, segment=segment, owner_id=user, input=value)
    )
    return segment


def read_input(session: Session, run, segment):
    return session.execute(
        select(agent_run_inputs.c.owner_id, agent_run_inputs.c.input).where(
            agent_run_inputs.c.run_id == run, agent_run_inputs.c.segment == segment
        )
    ).first()


def owned_segment(session: Session, user, run):
    """The newest segment of an owned run, or None."""
    return session.execute(
        select(func.max(agent_run_inputs.c.segment)).where(
            agent_run_inputs.c.run_id == run, agent_run_inputs.c.owner_id == user
        )
    ).scalar_one()


def append_event(session: Session, run, segment, event) -> int:
    seq = session.execute(
        select(func.coalesce(func.max(agent_run_events.c.seq), 0) + 1).where(
            agent_run_events.c.run_id == run, agent_run_events.c.segment == segment
        )
    ).scalar_one()
    session.execute(
        agent_run_events.insert().values(run_id=run, segment=segment, seq=seq, event=event)
    )
    session.execute(
        text("select pg_notify(:channel, :run)"), {"channel": EVENTS_CHANNEL, "run": str(run)}
    )
    return seq


def events_after(session: Session, run, segment, after, limit=200):
    return list(
        session.execute(
            select(agent_run_events.c.seq, agent_run_events.c.event)
            .where(
                agent_run_events.c.run_id == run,
                agent_run_events.c.segment == segment,
                agent_run_events.c.seq > after,
            )
            .order_by(agent_run_events.c.seq)
            .limit(limit)
        ).tuples()
    )


def newest_event(session: Session, run, segment, through):
    """The newest of the segment's first `through` events, or None."""
    return session.execute(
        select(agent_run_events.c.event)
        .where(
            agent_run_events.c.run_id == run,
            agent_run_events.c.segment == segment,
            agent_run_events.c.seq <= through,
        )
        .order_by(agent_run_events.c.seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def last_event(session: Session, run):
    """The number of events in the run's newest segment."""
    newest = (
        select(func.max(agent_run_inputs.c.segment))
        .where(agent_run_inputs.c.run_id == run)
        .scalar_subquery()
    )
    return session.execute(
        select(func.max(agent_run_events.c.seq)).where(
            agent_run_events.c.run_id == run, agent_run_events.c.segment == newest
        )
    ).scalar_one()


def interrupt(session: Session, run):
    """A run that can't end by itself (its worker died, or its finish failed) ends failed; it
    is never re-run by itself. Returns the run's owner and mode, or None when it was not
    running."""
    return session.execute(
        agent_runs.update()
        .where(agent_runs.c.id == run, agent_runs.c.status == "running")
        .values(
            status="failed",
            end_reason="failed",
            ended_at=func.now(),
            token_sha256=None,
            token_expires_at=None,
        )
        .returning(agent_runs.c.started_by, agent_runs.c.mode)
    ).first()


def cancel_paused(session: Session, user, run) -> bool:
    """End an owned paused run as cancelled; False for any other run."""
    cancelled = session.execute(
        agent_runs.update()
        .where(
            agent_runs.c.id == run,
            agent_runs.c.started_by == user,
            agent_runs.c.status == "paused",
        )
        .values(status="cancelled", end_reason="cancelled", ended_at=func.now())
        .returning(agent_runs.c.id)
    ).first()
    return cancelled is not None


def prune_events(session: Session, hours: int) -> None:
    """Events and inputs go a day after their run ends; messages stay the lasting record. A
    running or paused run keeps them all, so a resume still finds its input."""
    live = select(agent_runs.c.id).where(agent_runs.c.status.in_(["running", "paused"]))
    ended = select(agent_runs.c.id).where(
        agent_runs.c.ended_at < func.now() - func.make_interval(0, 0, 0, 0, hours)
    )
    orphaned = agent_run_inputs.c.created_at < func.now() - func.make_interval(
        0, 0, 0, 0, hours * 2
    )
    stale = select(agent_run_inputs.c.run_id).where(
        agent_run_inputs.c.run_id.in_(ended) | orphaned, agent_run_inputs.c.run_id.not_in(live)
    )
    session.execute(agent_run_events.delete().where(agent_run_events.c.run_id.in_(stale)))
    session.execute(agent_run_inputs.delete().where(agent_run_inputs.c.run_id.in_(stale)))


def _lock_account(session: Session, user) -> None:
    session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": "run-account:" + str(user)},
    )


# The longest a run segment may run. settings.agent_run_seconds, queue.GRACE and the chart's
# worker stopSeconds repeat it, since infrastructure doesn't import agent.
RUN_SECONDS = 900
# Longer than any run segment can take (RUN_SECONDS plus its wait for a worker).
SEGMENT_SECONDS = RUN_SECONDS + 300


def _recent():
    """Runs with a segment queued within SEGMENT_SECONDS: only these can still be running."""
    return select(agent_run_inputs.c.run_id).where(
        agent_run_inputs.c.created_at
        > func.now() - func.make_interval(0, 0, 0, 0, 0, 0, SEGMENT_SECONDS)
    )


def stalled(session: Session) -> list:
    """Runs left "running" past any segment's time: their finish failed, unless a job still
    holds them (service.end_stalled checks)."""
    running = select(agent_runs.c.id).where(
        agent_runs.c.status == "running", agent_runs.c.id.not_in(_recent())
    )
    return list(session.execute(running).scalars())


def _refuse_second_task(session: Session, user) -> None:
    """One running task-mode run per person, so a few people can't fill the agent worker.
    A run counts only while its newest segment is recent: a row left "running" by a lost
    database connection must not lock its owner out of task mode."""
    running = select(agent_runs.c.id).where(
        agent_runs.c.started_by == user,
        agent_runs.c.mode == "task",
        agent_runs.c.status == "running",
        agent_runs.c.id.in_(_recent()),
    )
    if session.execute(running.limit(1)).first() is not None:
        raise RateLimited()


def start(session: Session, user, conversation, identifier, model, version, mode="chat"):
    _lock_account(session, user)
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
    if mode == "task":
        _refuse_second_task(session, user)
    session.execute(
        agent_runs.insert().values(
            id=identifier,
            started_by=user,
            conversation_id=conversation,
            status="running",
            model=model,
            instructions_version=version,
            mode=mode,
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


def resume(session: Session, user, identifier) -> str:
    """Run a paused run again; returns its mode."""
    _lock_account(session, user)
    mode = session.execute(
        select(agent_runs.c.mode).where(
            agent_runs.c.id == identifier, agent_runs.c.started_by == user
        )
    ).scalar_one()
    if mode == "task":
        _refuse_second_task(session, user)
    session.execute(
        agent_runs.update()
        .where(agent_runs.c.id == identifier, agent_runs.c.started_by == user)
        .values(status="running", ended_at=None)
    )
    return mode


def finish(
    session: Session,
    user,
    identifier,
    conversation,
    status,
    state,
    *,
    tool_calls=None,
    active_seconds=None,
    end_reason=None,
):
    """End a run and record counters only (no prompts, answers or tool arguments).
    Returns the run's mode, or None when it was no longer running."""
    row = session.execute(
        select(agent_runs.c.mode)
        .where(
            agent_runs.c.id == identifier,
            agent_runs.c.started_by == user,
            agent_runs.c.status == "running",
        )
        .with_for_update()
    ).first()
    if row is None:
        return None
    statement = insert(agent_sessions).values(conversation_id=conversation, state=state)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[agent_sessions.c.conversation_id],
            set_={"state": state, "updated_at": func.now()},
        )
    )
    values = {
        "status": status,
        "ended_at": func.now(),
        "token_sha256": None,
        "token_expires_at": None,
        "end_reason": end_reason or status,
    }
    if tool_calls is not None:
        values["tool_calls"] = tool_calls
    if active_seconds is not None:
        values["active_seconds"] = active_seconds
    session.execute(agent_runs.update().where(agent_runs.c.id == identifier).values(**values))
    return row.mode


def issue_token(session, user, identifier, digest, expires, context):
    """Store the running run's token digest, and its transport ids in the session state."""
    conversation = session.execute(
        agent_runs.update()
        .where(
            agent_runs.c.id == identifier,
            agent_runs.c.started_by == user,
            agent_runs.c.status == "running",
        )
        .values(token_sha256=digest, token_expires_at=expires)
        .returning(agent_runs.c.conversation_id)
    ).scalar_one_or_none()
    if conversation is None:
        from app.infrastructure.errors import NotFound

        raise NotFound()
    # Merged in the database, so the thread snapshot beside it is neither read nor rewritten.
    statement = insert(agent_sessions).values(
        conversation_id=conversation, state={"craftTransport": context}
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[agent_sessions.c.conversation_id],
            set_={
                "state": agent_sessions.c.state.op("||")(statement.excluded.state),
                "updated_at": func.now(),
            },
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
            select(
                agent_runs.c.id,
                agent_runs.c.started_by,
                agent_runs.c.conversation_id,
                agent_sessions.c.state["craftTransport"].label("transport"),
            )
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
