"""Job rows through job_overview and Procrastinate's SQL functions, inside the caller's session."""

import json

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    Index,
    Integer,
    Table,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert
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

queue_samples = Table(
    "queue_samples",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("sampled_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("queue", Text, nullable=False),
    Column("queued", Integer, nullable=False),
    Column("running", Integer, nullable=False),
    Column("workers_alive", Integer, nullable=False),
    Column("concurrency", Integer, nullable=False),
    Column("oldest_wait_seconds", Double),
    CheckConstraint(
        "queued >= 0 AND running >= 0 AND workers_alive >= 0 AND concurrency >= 1",
        name="counts",
    ),
)
Index("ix_queue_samples_queue_sampled_at", queue_samples.c.queue, queue_samples.c.sampled_at)

# How far a running job got, as the job reports it. note is a fixed phrase from code.
job_progress = Table(
    "job_progress",
    metadata,
    Column("job_id", BigInteger, primary_key=True),
    Column("done", Integer, nullable=False),
    Column("total", Integer, nullable=False),
    Column("note", Text),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint("done >= 0 AND total >= 0", name="counts"),
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


def save_progress(session: Session, job_id: int, done: int, total: int, note: str | None) -> None:
    """Insert or replace the job's one progress row."""
    statement = insert(job_progress).values(job_id=job_id, done=done, total=total, note=note)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[job_progress.c.job_id],
            set_={"done": done, "total": total, "note": note, "updated_at": func.now()},
        )
    )


def progress(session: Session, job_id: int):
    """The job's progress (done, total, note), or None if it reported none."""
    return (
        session.execute(
            select(job_progress.c.done, job_progress.c.total, job_progress.c.note).where(
                job_progress.c.job_id == job_id
            )
        )
        .mappings()
        .first()
    )


def prune_progress(session: Session, days: int) -> None:
    """Delete progress rows not updated for `days` days."""
    session.execute(
        job_progress.delete().where(
            job_progress.c.updated_at < func.now() - func.make_interval(0, 0, 0, days)
        )
    )


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


def cancel_queued(session: Session, job_id: int) -> bool:
    """Cancel the job only if no worker has claimed it yet, in one statement."""
    cancelled = session.execute(
        text("select procrastinate_cancel_job_v1(:id, false, false)"), {"id": job_id}
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


def sample_queues(session: Session, queues: list[str], concurrency: int, stalled: float) -> None:
    """Write one sample per queue: depth, alive workers, this worker's concurrency."""
    alive = session.execute(
        text(
            "select count(*) from procrastinate_workers"
            " where last_heartbeat > now() - make_interval(secs => :stalled)"
        ),
        {"stalled": stalled},
    ).scalar_one()
    for queue in queues:
        row = (
            session.execute(
                text(
                    "select"
                    " count(*) filter (where state = 'queued') as queued,"
                    " count(*) filter (where state = 'started') as running,"
                    " extract(epoch from now() - min(enqueued_at)"
                    "   filter (where state = 'queued')) as oldest"
                    " from job_overview where queue = :queue"
                    "   and state in ('queued', 'started', 'stopping')"
                ),
                {"queue": queue},
            )
            .mappings()
            .one()
        )
        session.execute(
            text(
                "insert into queue_samples"
                " (queue, queued, running, workers_alive, concurrency, oldest_wait_seconds)"
                " values (:queue, :queued, :running, :alive, :concurrency, :oldest)"
            ),
            {
                "queue": queue,
                "queued": int(row["queued"] or 0),
                "running": int(row["running"] or 0),
                "alive": int(alive),
                "concurrency": concurrency,
                "oldest": float(row["oldest"]) if row["oldest"] is not None else None,
            },
        )


def job_percentiles(session: Session, since_seconds: float):
    """Wait and run percentiles per queue for jobs finished in the window."""
    return list(
        session.execute(
            text(
                """
                select queue,
                       count(*) as jobs,
                       percentile_cont(0.5) within group (
                         order by wait_seconds
                       ) as wait_p50,
                       percentile_cont(0.95) within group (
                         order by wait_seconds
                       ) as wait_p95,
                       max(wait_seconds) as wait_max,
                       percentile_cont(0.5) within group (
                         order by run_seconds
                       ) as run_p50,
                       percentile_cont(0.95) within group (
                         order by run_seconds
                       ) as run_p95,
                       max(run_seconds) as run_max
                  from job_metrics
                 where finished_at is not null
                   and wait_seconds is not null
                   and run_seconds is not null
                   and finished_at >= now() - make_interval(secs => :since)
                 group by queue
                """
            ),
            {"since": since_seconds},
        ).mappings()
    )


def kind_stats(session: Session, since_seconds: float):
    """Per queue and kind: counts, wait/run percentiles, failures by error class."""
    return list(
        session.execute(
            text(
                """
                select queue, kind, outcome, error, count(*) as jobs,
                       percentile_cont(0.5) within group (
                         order by wait_seconds
                       ) as wait_p50,
                       percentile_cont(0.95) within group (
                         order by wait_seconds
                       ) as wait_p95,
                       max(wait_seconds) as wait_max,
                       percentile_cont(0.5) within group (
                         order by run_seconds
                       ) as run_p50,
                       percentile_cont(0.95) within group (
                         order by run_seconds
                       ) as run_p95,
                       max(run_seconds) as run_max
                  from job_metrics
                 where coalesce(finished_at, enqueued_at)
                         >= now() - make_interval(secs => :since)
                 group by queue, kind, outcome, error
                """
            ),
            {"since": since_seconds},
        ).mappings()
    )


def sample_stats(session: Session, since_seconds: float, sample_seconds: float = 30.0):
    """Peak queued and seconds saturated (waiting while every slot was busy)."""
    return list(
        session.execute(
            text(
                """
                with buckets as (
                  select distinct on (
                    queue,
                    to_timestamp(floor(extract(epoch from sampled_at) / :step) * :step)
                  )
                    queue, queued, running, concurrency, workers_alive,
                    to_timestamp(floor(extract(epoch from sampled_at) / :step) * :step) as bucket
                  from queue_samples
                  where sampled_at >= now() - make_interval(secs => :since)
                  order by queue,
                    to_timestamp(floor(extract(epoch from sampled_at) / :step) * :step),
                    queued desc
                )
                select queue,
                       max(queued) as peak_queued,
                       coalesce(
                         count(*) filter (
                           where queued > 0 and running >= concurrency
                         ), 0
                       ) * :step as saturated_seconds,
                       max(workers_alive) as workers_alive,
                       max(concurrency) as concurrency
                  from buckets
                 group by queue
                """
            ),
            {"since": since_seconds, "step": sample_seconds},
        ).mappings()
    )


def workers_alive(session: Session, stalled: float) -> int:
    return session.execute(
        text(
            "select count(*) from procrastinate_workers"
            " where last_heartbeat > now() - make_interval(secs => :stalled)"
        ),
        {"stalled": stalled},
    ).scalar_one()
