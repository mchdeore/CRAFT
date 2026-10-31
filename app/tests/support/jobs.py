"""Test-only job operations, which `python -m app.tests.support.job_worker` runs, and the `jobs`
fixture that defers and watches them."""

import asyncio
import time
from uuid import uuid4

import pytest
from procrastinate.exceptions import JobAborted
from sqlalchemy import text

from app.infrastructure import queue
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.operations import Job, operation
from app.infrastructure.operations.jobs import current_job, enqueue, report_progress, should_abort
from app.infrastructure.shapes import Shape


class GeneratedFailure(Exception):
    """A test job's failure, recorded by its class name."""


class Sleep(Shape):
    seconds: float = 0
    fail: bool = False
    key: str = ""


@operation(SIGNED_IN, job=Job(queue="interactive", lock=lambda request: request.key))
async def sleep(caller: Caller, request: Sleep) -> None:
    """Sleep, then fail when asked to."""
    assert current_job.get() is not None
    await asyncio.sleep(request.seconds)
    if request.fail:
        raise GeneratedFailure()


@operation(SIGNED_IN, job=Job(queue="bulk"))
async def bulk(caller: Caller, request: Sleep) -> None:
    """Sleep in the bulk queue."""
    await asyncio.sleep(request.seconds)


@operation(SIGNED_IN, job=Job(queue="agent"))
async def agent(caller: Caller) -> None:
    """Finish at once in the agent queue."""


@operation(SIGNED_IN, job=Job(queue="interactive"))
def blocking(caller: Caller, request: Sleep) -> None:
    """A blocking job that stops between steps when asked to."""
    deadline = time.monotonic() + request.seconds
    while time.monotonic() < deadline:
        if should_abort():
            raise JobAborted()
        time.sleep(0.05)


@operation("accounts.manage", job=Job(queue="interactive"))
def managed(caller: Caller) -> None:
    """Only a manager's job may run."""


class Count(Shape):
    total: int
    fail: bool = False
    seconds: float = 0


@operation(SIGNED_IN, job=Job(queue="interactive", notify=True))
def counted(caller: Caller, request: Count) -> None:
    """Report progress on every step, wait `seconds` (stopping when asked to), then finish or
    fail when asked to."""
    for done in range(1, request.total + 1):
        report_progress(done, request.total, "Counting")
    deadline = time.monotonic() + request.seconds
    while time.monotonic() < deadline:
        if should_abort():
            raise JobAborted()
        time.sleep(0.05)
    if request.fail:
        raise GeneratedFailure()


@pytest.fixture
def jobs(settings, owner_engine, monkeypatch):
    monkeypatch.setattr(queue, "POLL_SECONDS", 0.1)
    with owner_engine.connect() as connection:
        newest = connection.execute(
            text("select coalesce(max(id), 0) from procrastinate_jobs")
        ).scalar_one()
    engine = make_engine(settings)
    sessions = made = make_sessions(engine)

    class Jobs:
        last_job_id = newest  # jobs above it are this test's own
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
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "update procrastinate_jobs set status = 'cancelled'"
                " where status = 'todo' and id > :last"
            ),
            {"last": newest},
        )
