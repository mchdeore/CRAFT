"""Job rows through job_overview and Procrastinate's SQL functions, inside the caller's session."""

import json

from sqlalchemy import BigInteger, Column, DateTime, Integer, Table, Text, text
from sqlalchemy.orm import Session

from app.infrastructure.database.metadata import metadata

job_attempt_errors = Table(
    "job_attempt_errors",
    metadata,
    Column("job_id", BigInteger, primary_key=True),
    Column("attempt", Integer, primary_key=True),
    Column("error", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)

COLUMNS = (
    "job_id, kind, queue, state, attempts, requested_by,"
    " enqueued_at, started_at, finished_at, error"
)


def active(session: Session, lock: str) -> int | None:
    """The queued or running job holding this key, after serializing on it."""
    session.execute(text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock})
    return session.execute(
        text(
            "select id from procrastinate_jobs"
            " where (queueing_lock = :key and status = 'todo')"
            " or (lock = :key and status = 'doing')"
            " order by id limit 1"
        ),
        {"key": lock},
    ).scalar_one_or_none()


def defer(session: Session, queue: str, kind: str, args: dict, lock: str | None) -> int:
    """Insert one job; workers hear of it by NOTIFY when the transaction commits."""
    return session.execute(
        text(
            "select unnest(procrastinate_defer_jobs_v1(array[row("
            " :queue, :kind, 0, :lock, :lock, cast(:args as jsonb), null"
            ")::procrastinate_job_to_defer_v1]))"
        ),
        {"queue": queue, "kind": kind, "lock": lock, "args": json.dumps(args)},
    ).scalar_one()


def read(session: Session, job_id: int):
    return (
        session.execute(
            text(f"select {COLUMNS}, args from job_overview where job_id = :id"), {"id": job_id}
        )
        .mappings()
        .first()
    )


def page(session: Session, owner: str | None, state: str | None, offset: int, limit: int):
    """One more row than the limit, so the caller knows whether another page exists."""
    return list(
        session.execute(
            text(
                f"select {COLUMNS} from job_overview"
                " where (cast(:owner as text) is null or requested_by = :owner)"
                " and (cast(:state as text) is null or state = :state)"
                " order by job_id desc offset :offset limit :limit"
            ),
            {"owner": owner, "state": state, "offset": offset, "limit": limit + 1},
        ).mappings()
    )


def cancel(session: Session, job_id: int) -> bool:
    """A queued job is cancelled; a running one is asked to stop."""
    cancelled = session.execute(
        text("select procrastinate_cancel_job_v1(:id, true, false)"), {"id": job_id}
    ).scalar_one()
    return cancelled is not None


def counts(session: Session):
    return list(
        session.execute(
            text(
                "select queue, state, count(*) as jobs,"
                " extract(epoch from now() - min(enqueued_at)) as oldest"
                " from job_overview group by queue, state"
            )
        ).mappings()
    )
