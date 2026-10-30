"""The PostgreSQL job queue with test-only job operations: no model calls."""

import asyncio
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from procrastinate.periodic import PeriodicDeferrer
from sqlalchemy import select, text

from app.infrastructure import queue
from app.infrastructure.access import PUBLIC, SIGNED_IN, Caller
from app.infrastructure.database.metadata import metadata
from app.infrastructure.errors import Forbidden
from app.infrastructure.operations import Job, operation
from app.infrastructure.operations.jobs import current_job
from app.infrastructure.paths import ROOT
from app.tests.support import jobs as test_jobs
from app.tests.support import model as scripted_model
from app.tests.support.cube import cube_source as cube_source

ORIGIN = {"Origin": "https://testserver"}


def test_a_lock_returns_the_active_job(jobs):
    key = "lock-" + str(time.time_ns())
    held = jobs.defer(test_jobs.sleep, key=key)
    assert jobs.defer(test_jobs.sleep, key=key) == held
    jobs.cancel(held)
    # A worker may have started it already; cancelling then takes effect when it stops.
    jobs.wait(held, {"canceled", "stopped", "finished", "failed"})
    assert jobs.defer(test_jobs.sleep, key=key) != held


def test_a_claimed_job_is_not_cancelled_as_queued(jobs, owner_engine):
    from app.jobs.handlers import cancel_queued_job

    held = jobs.defer(test_jobs.sleep, key="claimed-" + str(time.time_ns()))
    with owner_engine.begin() as connection:
        connection.execute(
            text("update procrastinate_jobs set status = 'doing' where id = :id"), {"id": held}
        )
    with jobs.sessions.begin() as session:
        assert cancel_queued_job(session, held) is False
    assert jobs.row(held).state == "started"
    jobs.cancel(held)


def test_calling_a_job_operation_queues_it_and_the_worker_logs_a_child_call(settings, jobs):
    caller = jobs.caller
    with queue.worker_thread(settings, ["interactive"]):
        job = int(test_jobs.sleep(caller, test_jobs.Sleep()).job_id)
        assert jobs.wait(job, {"finished", "failed"}).state == "finished"
        with pytest.raises(Forbidden):
            test_jobs.managed(caller)
        # Queued past the check (as if roles changed): the worker checks access again.
        refused = jobs.defer(test_jobs.managed)
        assert jobs.wait(refused, {"finished", "failed"}).error == "Forbidden"
    with jobs.sessions.begin() as session:
        rows = session.execute(
            text(
                "select call_id, parent_call_id, request_id, outcome, error_code from call_log"
                " where name = 'tests.sleep' and user_id = :user order by at"
            ),
            {"user": caller.user_id},
        ).all()
    queued, ran = rows
    assert ran.parent_call_id == queued.call_id and ran.request_id == queued.request_id
    assert (queued.outcome, ran.outcome) == ("ok", "ok")


def test_transient_failures_retry_and_permanent_ones_fail_once(settings, jobs):
    with queue.worker_thread(settings, ["interactive"]):
        transient = jobs.defer(test_jobs.sleep, fail="transient", times=2)
        permanent = jobs.defer(test_jobs.sleep, fail="permanent")
        done = jobs.wait(transient, {"finished", "failed"})
        failed = jobs.wait(permanent, {"finished", "failed"})
    assert (done.state, done.attempts) == ("finished", 3)  # every run counts, success too
    assert (failed.state, failed.attempts, failed.error) == ("failed", 1, "PermanentError")


def test_running_jobs_stop_through_the_api(settings, jobs, cube_source):
    with (
        queue.worker_thread(settings, ["interactive"]),
        TestClient(cube_source["app"], base_url="https://testserver") as http,
    ):
        assert cube_source["sign_in"](http).status_code == 200
        user = cube_source["caller"].user_id
        running = jobs.defer(test_jobs.sleep, requested_by=user, seconds=30)
        blocking = jobs.defer(test_jobs.blocking, requested_by=user, seconds=30)
        other = jobs.defer(test_jobs.sleep, seconds=0)
        jobs.wait(running, {"started"})
        jobs.wait(blocking, {"started"})
        listed = http.get("/api/jobs").json()["jobs"]
        assert {job["jobId"] for job in listed} >= {str(running), str(blocking)}
        assert str(other) not in {job["jobId"] for job in listed}
        assert http.get(f"/api/jobs/{other}").status_code == 404
        for job in (running, blocking):
            stopping = http.post(f"/api/jobs/{job}/cancel", headers=ORIGIN)
            assert stopping.status_code == 200
            assert stopping.json()["status"] in {"stopping", "stopped"}
            assert jobs.wait(job, {"stopped", "failed", "finished"}).state == "stopped"
        retried = http.post(f"/api/jobs/{running}/retry", headers=ORIGIN)
        assert retried.status_code == 200 and retried.json()["jobId"] != str(running)
        jobs.cancel(int(retried.json()["jobId"]))
        assert http.get("/health/queue").json()["queues"][0]["queue"] == "agent"


def test_a_long_bulk_job_never_delays_the_agent_queue(settings, jobs):
    with (
        queue.worker_thread(settings, ["bulk"], concurrency=1),
        queue.worker_thread(settings, ["agent"]),
    ):
        long = jobs.defer(test_jobs.bulk, seconds=30)
        jobs.wait(long, {"started"})
        quick = jobs.defer(test_jobs.agent)
        jobs.wait(quick, {"finished"}, seconds=5)
        assert jobs.row(long).state == "started"
        jobs.cancel(long)
        jobs.wait(long, {"stopped"})


@pytest.mark.slow
def test_a_killed_workers_job_is_retried_and_finishes(settings, jobs, monkeypatch):
    environment = (
        os.environ
        | scripted_model.ENVIRONMENT
        | {
            "CRAFT_DATABASE_NAME": settings.database_name,
            "CRAFT_WORKER_QUEUES": "interactive",
        }
    )
    worker = subprocess.Popen(
        [sys.executable, "-m", "app.tests.support.job_worker"], cwd=ROOT, env=environment
    )
    try:
        job = jobs.defer(test_jobs.sleep, seconds=60)
        jobs.wait(job, {"started"}, seconds=30)
    finally:
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait()
    monkeypatch.setattr(queue, "STALLED_SECONDS", 2.0)
    monkeypatch.setattr(queue, "RECOVER_SECONDS", 0.5)
    with queue.worker_thread(settings, ["interactive"]):
        done = jobs.wait(job, {"finished", "failed"}, seconds=30)
    assert (done.state, done.attempts, done.error) == ("finished", 2, "WorkerLost")


def test_a_failing_recovery_hook_is_logged_and_the_lost_worker_still_recorded(
    settings, monkeypatch, caplog
):
    def interrupted(sessions, args):
        raise RuntimeError("hook failed")

    kind = queue.Kind("test.lost", lambda: None, "agent", None, False, None, interrupted)
    monkeypatch.setitem(queue.KINDS, kind.name, kind)
    recorded = []
    monkeypatch.setattr(
        queue,
        "_record_failure",
        lambda _, job_id, attempt, error: recorded.append((job_id, type(error).__name__)),
    )
    lost = SimpleNamespace(id=1, task_name=kind.name, queue="agent", attempts=0, task_kwargs={})

    class Manager:
        async def get_stalled_jobs(self, seconds_since_heartbeat):
            return [lost]

        async def finish_job(self, job, status, delete_job):
            pass

    app = cast(Any, SimpleNamespace(job_manager=Manager()))
    with caplog.at_level("WARNING", logger="craft.jobs"):
        assert asyncio.run(queue.recover(app, settings, cast(Any, None))) == 1
    assert recorded == [(1, "WorkerLost")]
    warnings = [record.getMessage() for record in caplog.records]
    assert warnings == ["job recovery hook failed", "job recovered"]


TICKS: list[int] = []
TICK = {"seconds": 0.0}


@operation(PUBLIC, job=Job(queue="bulk", retry=False, every="* * * * *"))
async def tick(caller: Caller) -> None:
    """A scheduled job that records each run."""
    job = current_job.get()
    assert job is not None
    TICKS.append(job.id)
    await asyncio.sleep(TICK["seconds"])


@pytest.fixture
def person(owner_engine):
    users = metadata.tables["users"]
    with owner_engine.begin() as connection:
        user = connection.execute(
            users.insert()
            .values(username="jobs_" + uuid4().hex, display_name="Synthetic job account")
            .returning(users.c.id)
        ).scalar_one()
    yield user
    notifications = metadata.tables["notifications"]
    with owner_engine.begin() as connection:
        connection.execute(notifications.delete().where(notifications.c.user_id == user))
        connection.execute(users.delete().where(users.c.id == user))


def test_a_scheduled_job_is_public_with_no_input():
    def signed_in(caller: Caller) -> None:
        """Not a scheduled job."""

    for access, job in [
        (SIGNED_IN, Job(queue="bulk", retry=False, every="* * * * *")),
        # A retry would put the job back in the queue beside the next tick's.
        (PUBLIC, Job(queue="bulk", every="* * * * *")),
        (PUBLIC, Job(queue="bulk", retry=False, every="* * * * *", notify=True)),
        (PUBLIC, Job(queue="bulk", retry=False, every="often")),
    ]:
        with pytest.raises(ValueError):
            operation(access, job=job)(signed_in)


def test_two_workers_queue_a_scheduled_job_once_per_tick(settings, jobs, owner_engine):
    name = tick.__operation__.name
    apps = [queue.make_app(settings, jobs.sessions) for _ in range(2)]
    # A tick no earlier run of this test has used.
    at = 10**10 + uuid4().int % 10**9

    async def each_worker_defers(timestamp):
        for app in apps:
            async with app.open_async():
                periodic = app.periodic_registry.periodic_tasks[(name, "")]
                await PeriodicDeferrer(app.periodic_registry).defer_jobs([(periodic, timestamp)])

    def waiting():
        with owner_engine.connect() as connection:
            return list(
                connection.execute(
                    text(
                        "select id from procrastinate_jobs"
                        " where task_name = :name and status = 'todo' and id > :last"
                    ),
                    {"name": name, "last": jobs.last_job_id},
                ).scalars()
            )

    asyncio.run(each_worker_defers(at))
    queued = waiting()
    assert len(queued) == 1
    # The next tick finds that job still waiting and queues no second one.
    asyncio.run(each_worker_defers(at + 60))
    assert waiting() == queued
    jobs.cancel(queued[0])


@pytest.mark.slow
def test_a_tick_during_a_run_waits_for_it_and_runs_as_the_system(
    settings, jobs, owner_engine, monkeypatch, last_call_id
):
    name = tick.__operation__.name
    monkeypatch.setitem(TICK, "seconds", 3.0)
    TICKS.clear()
    app = queue.make_app(settings, jobs.sessions)
    periodic = app.periodic_registry.periodic_tasks[(name, "")]
    at = 2 * 10**10 + uuid4().int % 10**9

    def defer(timestamp):
        async def run():
            async with app.open_async():
                await PeriodicDeferrer(app.periodic_registry).defer_jobs([(periodic, timestamp)])

        asyncio.run(run())
        with owner_engine.connect() as connection:
            return connection.execute(
                text("select max(id) from procrastinate_jobs where task_name = :name"),
                {"name": name},
            ).scalar_one()

    import io

    from app.infrastructure.logging import configure_logging

    stream = io.StringIO()
    configure_logging("INFO", stream)
    # Two job slots, so only the lock keeps the second tick from overlapping the first.
    with queue.worker_thread(settings, ["bulk"], concurrency=2):
        first = defer(at)
        jobs.wait(first, {"started"})
        second = defer(at + 60)
        time.sleep(0.5)
        assert (jobs.row(first).state, jobs.row(second).state) == ("started", "queued")
        jobs.wait(second, {"finished"})
    assert TICKS == [first, second]
    ran = [line for line in stream.getvalue().splitlines() if f'"kind":"{name}"' in line]
    assert len(ran) == 2 and all('"scheduled":true' in line for line in ran)
    call_log = metadata.tables["call_log"]
    with owner_engine.connect() as connection:
        users = connection.execute(
            select(call_log.c.user_id, call_log.c.origin).where(
                call_log.c.name == name, call_log.c.id > last_call_id
            )
        ).all()
    assert users and set(users) == {(None, "python")}


def test_a_job_reports_progress_and_notifies_its_requester(settings, jobs, person):
    from app.jobs.handlers import JobProgress, JobRef, read_job

    with queue.worker_thread(settings, ["interactive"]):
        done = jobs.defer(test_jobs.counted, requested_by=person, total=5)
        failed = jobs.defer(test_jobs.counted, requested_by=person, total=2, fail=True)
        jobs.wait(done, {"finished"})
        jobs.wait(failed, {"failed"})
    caller = Caller(user_id=person, roles=frozenset({"member"}), sessions=jobs.sessions)
    progress = read_job(caller, JobRef(job_id=str(done))).progress
    assert progress == JobProgress(done=5, total=5, note="Counting")
    notifications = metadata.tables["notifications"]
    with jobs.sessions.begin() as session:
        written = set(
            session.execute(
                select(
                    notifications.c.kind, notifications.c.ref_kind, notifications.c.ref_id
                ).where(notifications.c.user_id == person)
            ).tuples()
        )
    assert written == {("job_done", "job", str(done)), ("job_failed", "job", str(failed))}


@dataclass
class StubRun:
    """A RunningJob without a worker: report_progress needs only its id and throttle."""

    sessions: Any = None
    settings: Any = None
    id: int = 7
    attempt: int = 1
    progress_at: list[float] = field(default_factory=lambda: [0.0])

    def should_abort(self) -> bool:
        return False


def test_progress_is_checked_and_written_at_most_once_a_second(monkeypatch):
    from app.infrastructure.operations.jobs import report_progress
    from app.jobs import handlers

    with pytest.raises(RuntimeError):
        report_progress(1, 2)
    written = []
    monkeypatch.setattr(handlers, "save_progress", lambda *args: written.append(args[2:]))
    running = current_job.set(StubRun())
    try:
        for bad in [(3, 2, None), (-1, 2, None), (1, 2, "x" * 81)]:
            with pytest.raises(ValueError):
                report_progress(*bad)
        for done in range(1, 11):
            report_progress(done, 10, "Counting")
    finally:
        current_job.reset(running)
    # The first step, then nothing within the second, then always the last step.
    assert written == [(1, 10, "Counting"), (10, 10, "Counting")]


def test_a_missed_tick_runs_once_late_and_an_older_one_not_at_all(settings, jobs):
    app = queue.make_app(settings, jobs.sessions)
    name = tick.__operation__.name
    deferrer = PeriodicDeferrer(app.periodic_registry)
    periodic = app.periodic_registry.periodic_tasks[(name, "")]
    # A worker that starts between ticks queues the last one, once.
    minute = 10**10 - 10**10 % 60
    assert list(deferrer.get_timestamps(periodic, None, minute + 30)) == [minute]
    assert list(deferrer.get_timestamps(periodic, minute, minute + 30)) == []
    # The daily prune at 03:17 is queued by a worker starting at 03:20 but not at 04:00:
    # a missed day waits for the next one (the prune is idempotent).
    prune = app.periodic_registry.periodic_tasks[("maintenance.prune", "")]
    day = 10**10 - 10**10 % 86400
    assert list(deferrer.get_timestamps(prune, None, day + 3 * 3600 + 20 * 60)) == [
        day + 3 * 3600 + 17 * 60
    ]
    assert list(deferrer.get_timestamps(prune, None, day + 4 * 3600)) == []


def test_a_notification_that_cannot_be_written_never_fails_the_job(settings, jobs):
    # The fixture's requester has no account row, so the notification insert fails.
    with queue.worker_thread(settings, ["interactive"]):
        job = jobs.defer(test_jobs.counted, total=1)
        assert jobs.wait(job, {"finished", "failed"}).state == "finished"


@pytest.mark.slow
def test_a_killed_workers_notify_job_fails_and_tells_its_requester(
    settings, jobs, person, monkeypatch
):
    environment = (
        os.environ
        | scripted_model.ENVIRONMENT
        | {
            "CRAFT_DATABASE_NAME": settings.database_name,
            "CRAFT_WORKER_QUEUES": "interactive",
        }
    )
    worker = subprocess.Popen(
        [sys.executable, "-m", "app.tests.support.job_worker"], cwd=ROOT, env=environment
    )
    try:
        job = jobs.defer(test_jobs.counted, requested_by=person, total=1, seconds=60)
        jobs.wait(job, {"started"}, seconds=30)
    finally:
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait()
    monkeypatch.setattr(queue, "STALLED_SECONDS", 2.0)
    monkeypatch.setattr(queue, "RECOVER_SECONDS", 0.5)
    with queue.worker_thread(settings, ["interactive"]):
        jobs.wait(job, {"finished", "failed"}, seconds=30)
        # Recovery records the lost worker just after it fails the job.
        deadline = time.monotonic() + 10
        while jobs.row(job).error is None and time.monotonic() < deadline:
            time.sleep(0.05)
    assert tuple(jobs.row(job))[::2] == ("failed", "WorkerLost")
    notifications = metadata.tables["notifications"]
    with jobs.sessions.begin() as session:
        written = list(
            session.execute(
                select(notifications.c.kind, notifications.c.ref_id).where(
                    notifications.c.user_id == person
                )
            ).tuples()
        )
    assert written == [("job_failed", str(job))]


def test_job_logs_and_error_bodies_carry_no_content_or_secrets(settings, jobs, person):
    import io

    import msgspec

    from app.infrastructure.config.env import read_env
    from app.infrastructure.logging import configure_logging
    from app.jobs.handlers import JobRef, read_job

    marker = "synthetic-content-" + uuid4().hex
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    with queue.worker_thread(settings, ["interactive"]):
        # The marker is job input: it reaches the job, never a log line or an error body.
        failed = jobs.defer(test_jobs.sleep, key=marker, fail="permanent")
        notified = jobs.defer(test_jobs.counted, requested_by=person, total=3)
        jobs.wait(failed, {"failed"})
        jobs.wait(notified, {"finished"})
    lines = stream.getvalue()
    secrets = [marker] + [
        value
        for key, value in read_env().items()
        if len(value) >= 8 and any(word in key for word in ("PASSWORD", "KEY", "TOKEN", "SECRET"))
    ]
    assert '"kind":"tests.sleep"' in lines and '"kind":"tests.counted"' in lines
    assert '"name":"notifications.job_ended"' in lines
    assert not [secret for secret in secrets if secret in lines]
    body = msgspec.json.encode(read_job(jobs.caller, JobRef(job_id=str(failed)))).decode()
    assert '"error":"PermanentError"' in body and marker not in body


def notified_jobs(sessions, user):
    notifications = metadata.tables["notifications"]
    with sessions.begin() as session:
        return list(
            session.execute(
                select(notifications.c.kind, notifications.c.ref_id).where(
                    notifications.c.user_id == user
                )
            ).tuples()
        )


@pytest.mark.slow
def test_a_shutdown_mid_job_notifies_but_the_owners_cancel_does_not(settings, jobs, person):
    with queue.worker_thread(settings, ["interactive"]):
        cancelled = jobs.defer(test_jobs.counted, requested_by=person, total=1, seconds=60)
        jobs.wait(cancelled, {"started"})
        jobs.cancel(cancelled)
        jobs.wait(cancelled, {"stopped"})
        interrupted = jobs.defer(test_jobs.counted, requested_by=person, total=1, seconds=60)
        jobs.wait(interrupted, {"started"})
    # Leaving the block stops the worker; after its grace it aborts the running job.
    assert jobs.row(interrupted).state == "stopped"
    assert notified_jobs(jobs.sessions, person) == [("job_failed", str(interrupted))]
