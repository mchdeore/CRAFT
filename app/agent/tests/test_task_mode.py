"""Task-mode runs on generated rows: one running per person, notified when they end or wait."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent import service, storage
from app.infrastructure.database.metadata import metadata
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import RateLimited
from app.tests.support.runs import add_run

runs, notifications = metadata.tables["agent_runs"], metadata.tables["notifications"]


def start(person, thread, mode):
    """Queue a run's first segment, then start it, as queue_run and the worker do."""
    run, user = uuid4(), person.caller.user_id
    with unit_of_work(person.caller.sessions) as session:
        storage.add_segment(session, user, run, thread, {})
    with unit_of_work(person.caller.sessions) as session:
        storage.start(session, user, thread, run, "scripted", "v", mode)


def notified(owner_engine, person):
    with owner_engine.connect() as connection:
        return set(
            connection.execute(
                select(notifications.c.kind, notifications.c.ref_id).where(
                    notifications.c.user_id == person.caller.user_id
                )
            ).tuples()
        )


def test_two_task_starts_at_once_let_one_through(person):
    def attempt(thread):
        try:
            start(person, thread, "task")
            return "started"
        except RateLimited:
            return "refused"

    with ThreadPoolExecutor(2) as pool:
        outcomes = sorted(pool.map(attempt, person.threads[:2]))
    assert outcomes == ["refused", "started"]
    # A chat run is not held back by the running task.
    start(person, person.threads[2], "chat")


def test_a_task_left_running_by_a_lost_connection_does_not_block_the_next(owner_engine, person):
    # Its newest segment was queued long before any run could still be going.
    add_run(owner_engine, person, person.threads[0], "running", "task", timedelta(hours=2))
    start(person, person.threads[1], "task")
    with pytest.raises(RateLimited):
        start(person, person.threads[2], "task")


def test_a_paused_task_resumes_only_when_no_other_task_runs(owner_engine, person):
    paused = add_run(owner_engine, person, person.threads[0], "paused", "task")
    other = add_run(owner_engine, person, person.threads[1], "running", "task")
    with pytest.raises(RateLimited), unit_of_work(person.caller.sessions) as session:
        storage.resume(session, person.caller.user_id, paused)
    with owner_engine.begin() as connection:
        connection.execute(runs.update().where(runs.c.id == other).values(status="finished"))
    with unit_of_work(person.caller.sessions) as session:
        # The run keeps the mode it started with.
        assert storage.resume(session, person.caller.user_id, paused) == "task"


@pytest.mark.parametrize(
    ("status", "mode", "expected"),
    [
        ("finished", "task", {"run_done"}),
        ("failed", "task", {"run_done"}),
        ("paused", "task", {"run_waiting"}),
        ("cancelled", "task", set()),
        ("finished", "chat", set()),
    ],
)
def test_only_a_task_run_that_ends_or_waits_notifies(owner_engine, person, status, mode, expected):
    run = add_run(owner_engine, person, person.threads[0], "running", mode)
    request = SimpleNamespace(run_id=run, thread_id=person.threads[0])
    service.finish(person.caller, request, status, {})
    assert notified(owner_engine, person) == {(kind, str(run)) for kind in expected}
    # Finishing again (a duplicate end) changes nothing and writes no second notification.
    service.finish(person.caller, request, status, {})
    assert notified(owner_engine, person) == {(kind, str(run)) for kind in expected}


def test_a_task_run_lost_with_its_worker_notifies_once(owner_engine, person):
    task = add_run(owner_engine, person, person.threads[0], "running", "task")
    chat = add_run(owner_engine, person, person.threads[1], "running", "chat")
    # The second interrupt of the task finds it no longer running.
    for run in (task, chat, task):
        service.interrupt(person.caller.sessions, run, 1)
    assert notified(owner_engine, person) == {("run_done", str(task))}


def test_a_run_credential_outlives_the_longest_run(owner_engine, person):
    run = add_run(owner_engine, person, person.threads[0], "running", "task")
    service.issue_token(person.caller, run)
    with owner_engine.connect() as connection:
        expires = connection.execute(
            select(runs.c.token_expires_at).where(runs.c.id == run)
        ).scalar_one()
    assert expires > datetime.now(UTC) + timedelta(seconds=storage.RUN_SECONDS)


def test_a_lost_worker_ends_its_run_through_a_logged_operation(owner_engine, person):
    from sqlalchemy import func

    from app.agent.jobs import AgentRunJob, interrupted

    run = add_run(owner_engine, person, person.threads[0], "running", "task")
    call_log = metadata.tables["call_log"]
    with owner_engine.connect() as connection:
        before = connection.execute(select(func.max(call_log.c.id))).scalar_one() or 0
    interrupted(person.caller.sessions, AgentRunJob(run=run, segment=1))
    with owner_engine.connect() as connection:
        status = connection.execute(select(runs.c.status).where(runs.c.id == run)).scalar_one()
        logged = connection.execute(
            select(call_log.c.outcome).where(
                call_log.c.name == "agent.interrupt_run", call_log.c.id > before
            )
        ).scalars()
        assert status == "failed" and list(logged) == ["ok"]
    assert notified(owner_engine, person) == {("run_done", str(run))}
