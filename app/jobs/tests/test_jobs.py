"""The PostgreSQL job queue with test-only job operations: no model calls."""

import asyncio
import os
import signal
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from procrastinate.periodic import PeriodicDeferrer
from sqlalchemy import select, text

from app.infrastructure import queue
from app.infrastructure.access import PUBLIC, SIGNED_IN, Caller
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.database.metadata import metadata
from app.infrastructure.errors import Forbidden
from app.infrastructure.operations import Job, operation
from app.infrastructure.operations.jobs import current_job, enqueue
from app.infrastructure.paths import ROOT
from app.tests.support import jobs as test_jobs
from app.tests.support.cube import cube_source as cube_source

ORIGIN = {"Origin": "https://testserver"}


@pytest.fixture
def jobs(settings, owner_engine, monkeypatch):
    monkeypatch.setattr(queue, "POLL_SECONDS", 0.1)
    for name in ("interactive", "bulk"):
        monkeypatch.setitem(queue.WAITS, name, (0, 0, 0))
    with owner_engine.begin() as connection:
        connection.execute(
            text("update procrastinate_jobs set status = 'cancelled' where status = 'todo'")
        )
    engine = make_engine(settings)
    sessions = made = make_sessions(engine)

    class Jobs:
        caller = Caller(user_id=uuid4(), roles=frozenset({"member"}), sessions=made)
        sessions = made

        @staticmethod
        def defer(operation, requested_by=None, **values):
            caller = Jobs.caller
            if requested_by is not None:
                caller = Caller(user_id=requested_by, roles=caller.roles, sessions=sessions)
            declared = operation.__operation__
            request = declared.input(**values) if declared.input else None
            with sessions.begin() as session:
                return enqueue(session, caller, operation, request)

        @staticmethod
        def row(job_id):
            with owner_engine.connect() as connection:
                return connection.execute(
                    text("select state, attempts, error from job_overview where job_id = :id"),
                    {"id": job_id},
                ).one()

        @staticmethod
        def wait(job_id, states, seconds=10.0):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                row = Jobs.row(job_id)
                if row.state in states:
                    return row
                time.sleep(0.05)
            raise AssertionError(f"job {job_id} stayed {Jobs.row(job_id).state}")

        @staticmethod
        def cancel(job_id):
            with sessions.begin() as session:
                session.execute(
                    text("select procrastinate_cancel_job_v1(:id, true, false)"), {"id": job_id}
                )

    yield Jobs
    engine.dispose()


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
        started = time.monotonic()
        jobs.wait(quick, {"finished"}, seconds=5)
        assert time.monotonic() - started < 3
        jobs.cancel(long)
        jobs.wait(long, {"stopped"})


@pytest.mark.slow
def test_a_killed_workers_job_is_retried_and_finishes(settings, jobs, monkeypatch):
    environment = os.environ | {
        "CRAFT_DATABASE_NAME": settings.database_name,
        "CRAFT_WORKER_QUEUES": "interactive",
    }
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


TICKS: list[int] = []
TICK = {"seconds": 0.0}


@operation(PUBLIC, job=Job(queue="bulk", every="* * * * *"))
async def tick(caller: Caller) -> None:
    """A scheduled job that records each run."""
    TICKS.append(current_job.get().id)
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

    with pytest.raises(ValueError):
        operation(SIGNED_IN, job=Job(queue="bulk", every="* * * * *"))(signed_in)
    with pytest.raises(ValueError):
        operation(PUBLIC, job=Job(queue="bulk", every="often"))(signed_in)


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
                        " where task_name = :name and status = 'todo'"
                    ),
                    {"name": name},
                ).scalars()
            )

    asyncio.run(each_worker_defers(at))
    queued = waiting()
    assert len(queued) == 1
    # The next tick finds that job still waiting and queues no second one.
    asyncio.run(each_worker_defers(at + 60))
    assert waiting() == queued
    jobs.cancel(queued[0])


def test_a_tick_is_skipped_while_the_previous_run_goes(settings, jobs, monkeypatch):
    from app.infrastructure.operations.jobs import SCHEDULED
    from app.jobs import storage

    monkeypatch.setitem(TICK, "seconds", 1.0)
    TICKS.clear()

    def queue_tick():
        with jobs.sessions.begin() as session:
            args = SCHEDULED | {"timestamp": 0}
            return storage.defer(session, "bulk", tick.__operation__.name, args, None)

    with queue.worker_thread(settings, ["bulk"], concurrency=2):
        first = queue_tick()
        jobs.wait(first, {"started"})
        second = queue_tick()
        jobs.wait(second, {"finished"})
        assert jobs.row(first).state == "started"
        jobs.wait(first, {"finished"})
    assert TICKS == [first]


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
