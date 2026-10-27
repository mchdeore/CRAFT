"""The PostgreSQL job queue with test-only job kinds: no model calls."""

import os
import signal
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.infrastructure import queue
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.errors import NotFound
from app.infrastructure.paths import ROOT
from app.jobs.handlers import defer_job
from app.tests.support import jobs as test_jobs
from app.tests.support.cube import cube_source as cube_source

test_jobs.register()
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
    sessions = make_sessions(engine)

    class Jobs:
        @staticmethod
        def defer(kind, requested_by=None, **args):
            with sessions.begin() as session:
                return defer_job(session, kind, args, requested_by)

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


def test_unknown_kind_is_refused_and_a_lock_returns_the_active_job(jobs):
    with pytest.raises(NotFound):
        jobs.defer("test.unknown")
    key = "lock-" + str(time.time_ns())
    held = jobs.defer("test.sleep", key=key)
    assert jobs.defer("test.sleep", key=key) == held
    jobs.cancel(held)
    assert jobs.defer("test.sleep", key=key) != held


def test_transient_failures_retry_and_permanent_ones_fail_once(settings, jobs):
    with queue.worker_thread(settings, ["interactive"]):
        transient = jobs.defer("test.sleep", fail="transient", times=2)
        permanent = jobs.defer("test.sleep", fail="permanent")
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
        user = str(cube_source["caller"].user_id)
        running = jobs.defer("test.sleep", requested_by=user, seconds=30)
        blocking = jobs.defer("test.blocking", requested_by=user, seconds=30)
        other = jobs.defer("test.sleep", seconds=0)
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
        long = jobs.defer("test.bulk", seconds=30)
        jobs.wait(long, {"started"})
        quick = jobs.defer("test.agent")
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
        [sys.executable, "-m", "app.tests.support.jobs"], cwd=ROOT, env=environment
    )
    try:
        job = jobs.defer("test.sleep", seconds=60)
        jobs.wait(job, {"started"}, seconds=30)
    finally:
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait()
    monkeypatch.setattr(queue, "STALLED_SECONDS", 2.0)
    monkeypatch.setattr(queue, "RECOVER_SECONDS", 0.5)
    with queue.worker_thread(settings, ["interactive"]):
        done = jobs.wait(job, {"finished", "failed"}, seconds=30)
    assert (done.state, done.attempts, done.error) == ("finished", 2, "WorkerLost")
