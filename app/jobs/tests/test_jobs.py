"""The PostgreSQL job queue with test-only job operations: no model calls."""

import os
import signal
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.infrastructure import queue
from app.infrastructure.access import Caller
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.errors import Forbidden
from app.infrastructure.operations.jobs import enqueue
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
