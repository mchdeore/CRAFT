"""The daily prune deletes only rows past their keep time."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, text

from app.infrastructure.access import Caller
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.database.metadata import metadata
from app.maintenance.jobs import prune


def test_prune_deletes_expired_and_old_rows_only(settings, owner_engine):
    tables = metadata.tables
    users, sessions_table, tokens = (
        tables["users"],
        tables["auth_sessions"],
        tables["workspace_tokens"],
    )
    progress, notifications = tables["job_progress"], tables["notifications"]
    now = datetime.now(UTC)
    old_job, new_job = (-(10**12) - uuid4().int % 10**9 for _ in range(2))
    old, new, unread = (uuid4().hex for _ in range(3))
    with owner_engine.begin() as connection:
        user = connection.execute(
            users.insert()
            .values(username="pruned_" + uuid4().hex, display_name="Synthetic account")
            .returning(users.c.id)
        ).scalar_one()
    try:
        prune_once_and_check(
            settings, owner_engine, user, now, (old_job, new_job), (old, new, unread)
        )
    finally:
        with owner_engine.begin() as connection:
            connection.execute(progress.delete().where(progress.c.job_id.in_([old_job, new_job])))
            for table in (notifications, sessions_table, tokens):
                connection.execute(table.delete().where(table.c.user_id == user))
            connection.execute(users.delete().where(users.c.id == user))


def prune_once_and_check(settings, owner_engine, user, now, jobs, refs):
    tables = metadata.tables
    sessions_table, tokens = tables["auth_sessions"], tables["workspace_tokens"]
    progress, notifications = tables["job_progress"], tables["notifications"]
    old_job, new_job = jobs
    old, new, unread = refs
    with owner_engine.begin() as connection:
        for expires in (now - timedelta(hours=1), now + timedelta(hours=1)):
            connection.execute(
                sessions_table.insert().values(
                    user_id=user,
                    token_sha256=os.urandom(32),
                    idle_expires_at=min(expires, now),
                    expires_at=expires,
                )
            )
            connection.execute(
                tokens.insert().values(
                    user_id=user, token_sha256=os.urandom(32), label="Synthetic", expires_at=expires
                )
            )
        connection.execute(
            progress.insert(),
            [
                {"job_id": old_job, "done": 1, "total": 2, "updated_at": now - timedelta(days=31)},
                {"job_id": new_job, "done": 1, "total": 2, "updated_at": now - timedelta(days=1)},
            ],
        )
        for read_at, ref in ((now - timedelta(days=91), old), (now - timedelta(days=1), new)):
            connection.execute(
                notifications.insert().values(
                    user_id=user, kind="job_done", ref_kind="job", ref_id=ref, read_at=read_at
                )
            )
        connection.execute(
            notifications.insert().values(
                user_id=user,
                kind="job_done",
                ref_kind="job",
                ref_id=unread,
                created_at=now - timedelta(days=200),
            )
        )
    engine = make_engine(settings)
    try:
        # Twice: a duplicate run (a second tick, a second worker) deletes nothing more.
        for _ in range(2):
            prune.__operation__.run(Caller(sessions=make_sessions(engine), settings=settings))
    finally:
        engine.dispose()
    with owner_engine.connect() as connection:
        left = [
            connection.execute(select(table.c.expires_at).where(table.c.user_id == user)).scalars()
            for table in (sessions_table, tokens)
        ]
        assert [[at > now for at in rows] for rows in left] == [[True], [True]]
        kept = connection.execute(
            select(progress.c.job_id).where(progress.c.job_id.in_([old_job, new_job]))
        ).scalars()
        assert list(kept) == [new_job]
        refs_left = connection.execute(
            select(notifications.c.ref_id).where(notifications.c.user_id == user)
        ).scalars()
        assert sorted(refs_left) == sorted([new, unread])


def test_prune_keeps_a_month_of_jobs_and_samples_and_three_of_traffic(settings, owner_engine):
    """Ended jobs (with their errors) and queue samples go 30 days after they end or are taken,
    traffic counts after 90. A job's age is its end: one queued long before still stays."""
    now = datetime.now(UTC)
    task = "tests.history_" + uuid4().hex
    month, hour = timedelta(days=30), timedelta(hours=1)
    jobs = {}
    with owner_engine.begin() as connection:
        for name, ended in (("old", now - month - hour), ("recent", now - month + hour)):
            job = connection.execute(
                text(
                    "insert into procrastinate_jobs (queue_name, task_name, status, attempts)"
                    " values ('bulk', :task, 'failed', 1) returning id"
                ),
                {"task": task},
            ).scalar_one()
            # Queued 40 days ago (the insert's own 'deferred' event), then ended.
            connection.execute(
                text("update procrastinate_events set at = :at where job_id = :job"),
                {"job": job, "at": now - timedelta(days=40)},
            )
            connection.execute(
                text(
                    "insert into procrastinate_events (job_id, type, at)"
                    " values (:job, 'failed', :at)"
                ),
                {"job": job, "at": ended},
            )
            connection.execute(
                text(
                    "insert into job_attempt_errors (job_id, attempt, error) values (:job, 1, 'x')"
                ),
                {"job": job},
            )
            jobs[name] = job
        samples = [
            connection.execute(
                text(
                    "insert into queue_samples (sampled_at, queue, queued, running,"
                    " workers_alive, concurrency) values (:at, 'bulk', 0, 0, 1, 1) returning id"
                ),
                {"at": at},
            ).scalar_one()
            for at in (now - month - hour, now - month + hour)
        ]
        for at in (now - 3 * month - hour, now - 3 * month + hour):
            connection.execute(
                text(
                    "insert into traffic_log (at, operation, method, bytes_in, bytes_out,"
                    " duration_ms) values (:at, :task, 'GET', 0, 0, 1)"
                ),
                {"at": at, "task": task},
            )
    engine = make_engine(settings)
    try:
        prune.__operation__.run(Caller(sessions=make_sessions(engine), settings=settings))
        with owner_engine.connect() as connection:
            left = {
                table: set(connection.execute(text(query), {"task": task}).scalars())
                for table, query in {
                    "jobs": "select id from procrastinate_jobs where task_name = :task",
                    "traffic": "select extract(day from now() - at)::int from traffic_log"
                    " where operation = :task",
                }.items()
            }
            errors = connection.execute(
                text("select job_id from job_attempt_errors where job_id = any(:ids)"),
                {"ids": list(jobs.values())},
            ).scalars()
            kept_samples = connection.execute(
                text("select id from queue_samples where id = any(:ids)"), {"ids": samples}
            ).scalars()
            assert left["jobs"] == set(errors) == {jobs["recent"]}
            assert list(kept_samples) == samples[1:] and left["traffic"] == {89}
    finally:
        engine.dispose()
        with owner_engine.begin() as connection:
            connection.execute(
                text("delete from job_attempt_errors where job_id = any(:ids)"),
                {"ids": list(jobs.values())},
            )
            for query in (
                "delete from procrastinate_jobs where task_name = :task",
                "delete from traffic_log where operation = :task",
            ):
                connection.execute(text(query), {"task": task})
            connection.execute(
                text("delete from queue_samples where id = any(:ids)"), {"ids": samples}
            )
