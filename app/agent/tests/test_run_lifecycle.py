"""Every run ends: when its start races a cancel, when no worker takes it, when its input is
gone, when a person cancels it while it waits on an approval, and when its finish failed.
Its events and inputs go only after it ends."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import msgspec
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import create_app
from app.agent import runs, runtime, service, storage
from app.agent.handlers import AGUIEvent, RunAgentInput
from app.agent.jobs import AgentRunJob, agent_run, prune_run_events
from app.approvals.handlers import ApprovalRef, read_approval, request_approval
from app.infrastructure.database.metadata import metadata
from app.infrastructure.errors import Conflict
from app.infrastructure.operations.jobs import SCHEDULED, enqueue, runner
from app.jobs.handlers import active_job, cancel_queued_job
from app.tests.support.approvals import login
from app.tests.support.runs import add_run

agent_runs, inputs = metadata.tables["agent_runs"], metadata.tables["agent_run_inputs"]
events, call_log = metadata.tables["agent_run_events"], metadata.tables["call_log"]


def turn(thread):
    return RunAgentInput(
        thread_id=thread,
        run_id=uuid4(),
        messages=[{"id": str(uuid4()), "role": "user", "content": "Peak power"}],
    )


def worker_job(caller):
    """What a job operation sees of its run on the worker."""
    return SimpleNamespace(
        sessions=caller.sessions,
        settings=caller.settings,
        id=0,
        attempt=1,
        progress_at=[0.0],
        should_abort=lambda: False,
    )


def ended(owner_engine, run):
    with owner_engine.connect() as connection:
        found = connection.execute(
            select(agent_runs.c.status, agent_runs.c.end_reason).where(agent_runs.c.id == run)
        ).one()
    return tuple(found)


def test_a_producer_that_stops_short_still_ends_the_stream(monkeypatch):
    async def stopped(caller, request):
        yield AGUIEvent(type="RUN_STARTED")
        raise asyncio.CancelledError()

    monkeypatch.setattr(runtime, "_run", stopped)

    async def read():
        return [event async for event in runtime.run(None, None)]

    streamed = asyncio.run(asyncio.wait_for(read(), 5))
    assert [event.type for event in streamed] == ["RUN_STARTED", "RUN_ERROR"]
    assert streamed[-1].code == "internal_error"


def test_a_cancel_while_the_run_starts_still_ends_it(owner_engine, person, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = service.start

    def held(*args):
        state = original(*args)
        entered.set()
        release.wait(10)
        return state

    monkeypatch.setattr(service, "start", held)
    data = turn(person.threads[0])

    async def cancel_while_starting():
        iterator = runtime._run(person.caller, data)

        async def first():
            return await anext(iterator)

        task = asyncio.create_task(first())
        assert await asyncio.to_thread(entered.wait, 10)
        # The start has committed the run as running, and its thread is still busy.
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_while_starting())
    assert ended(owner_engine, data.run_id) == ("cancelled", "cancelled")


def test_a_run_no_worker_takes_is_cancelled_and_its_stream_ends(owner_engine, person, monkeypatch):
    monkeypatch.setattr(runs, "QUEUED_SECONDS", 0)
    monkeypatch.setattr(runs, "POLL_SECONDS", 0.01)
    data = turn(person.threads[0])
    segment, _ = service.queue_run(person.caller, data, False)

    async def follow():
        return [
            msgspec.to_builtins(event)
            async for event in runs.follow(
                person.caller, data.run_id, segment, 0, raise_errors=True
            )
        ]

    try:
        assert asyncio.run(asyncio.wait_for(follow(), 10)) == [service.CANCELLED]
        with person.caller.sessions.begin() as session:
            assert active_job(session, service.RUN_LOCK + str(data.run_id)) is None
    finally:
        with owner_engine.begin() as connection:
            connection.execute(events.delete().where(events.c.run_id == data.run_id))


def test_a_run_whose_input_is_gone_still_ends_its_stream(owner_engine, person):
    run = uuid4()
    caller = SCHEDULED["caller"] | {"user": str(person.caller.user_id)}
    job = msgspec.to_builtins(AgentRunJob(run=run, segment=1))
    try:
        asyncio.run(runner(agent_run.__operation__)(worker_job(person.caller), job, caller, None))
        assert service.events_after(person.caller, run, 1, 0) == [
            (1, {"type": "CRAFT_ERROR", "code": "not_found"})
        ]
    finally:
        with owner_engine.begin() as connection:
            connection.execute(events.delete().where(events.c.run_id == run))


def test_run_events_go_a_day_after_their_run_ends_but_never_while_it_waits(owner_engine, person):
    now = datetime.now(UTC)
    finished = add_run(owner_engine, person, person.threads[0], "finished", "chat")
    paused = add_run(owner_engine, person, person.threads[1], "paused", "chat", timedelta(hours=26))
    waiting = add_run(
        owner_engine, person, person.threads[2], "paused", "chat", timedelta(hours=49)
    )
    with owner_engine.begin() as connection:
        for run, hours in ((finished, 25), (paused, 25), (waiting, 49)):
            connection.execute(
                agent_runs.update()
                .where(agent_runs.c.id == run)
                .values(ended_at=now - timedelta(hours=hours))
            )
            connection.execute(
                events.insert().values(run_id=run, segment=1, seq=1, event={"type": "RUN_STARTED"})
            )
        # The person decided the approval and resumed: the fresh input waits for a worker.
        connection.execute(
            inputs.insert().values(
                run_id=paused, segment=2, owner_id=person.caller.user_id, input={}
            )
        )
        before = connection.execute(select(func.max(call_log.c.id))).scalar_one() or 0
    runner(prune_run_events.__operation__)(
        worker_job(person.caller), None, SCHEDULED["caller"], None
    )
    with owner_engine.connect() as connection:
        kept = {
            run: sorted(
                connection.execute(select(inputs.c.segment).where(inputs.c.run_id == run))
                .scalars()
                .all()
            )
            for run in (finished, paused, waiting)
        }
        logged = connection.execute(
            select(call_log.c.outcome).where(
                call_log.c.name == "agent.prune_run_events", call_log.c.id > before
            )
        ).scalars()
        left = connection.execute(
            select(events.c.run_id).where(events.c.run_id.in_([finished, paused, waiting]))
        ).scalars()
        assert set(left) == {paused, waiting}
        assert list(logged) == ["ok"]
    assert kept == {finished: [], paused: [1, 2], waiting: [1]}


def test_a_run_left_running_without_a_job_ends_failed_and_frees_its_conversation(
    owner_engine, person
):
    old = timedelta(seconds=storage.SEGMENT_SECONDS + 60)
    left = add_run(owner_engine, person, person.threads[0], "running", "chat", old)
    held = add_run(owner_engine, person, person.threads[1], "running", "chat", old)
    live = add_run(owner_engine, person, person.threads[2], "running", "chat")
    # A job that still holds its run's lock means the run may yet end by itself.
    with person.caller.sessions.begin() as session:
        job = enqueue(session, person.caller, agent_run, AgentRunJob(run=held, segment=1))
    try:
        runner(prune_run_events.__operation__)(
            worker_job(person.caller), None, SCHEDULED["caller"], None
        )
    finally:
        with person.caller.sessions.begin() as session:
            cancel_queued_job(session, job)
    assert ended(owner_engine, left) == ("failed", "failed")
    assert ended(owner_engine, held) == ended(owner_engine, live) == ("running", None)
    service.start(person.caller, turn(person.threads[0]), "simulated")
    with pytest.raises(Conflict):
        service.start(person.caller, turn(person.threads[1]), "simulated")


@pytest.mark.parametrize("pruned", [False, True])
def test_cancelling_a_paused_run_rejects_its_approval_and_frees_the_conversation(context, pruned):
    run, conversation, caller = context["run"], context["conversation"], context["caller"]
    approval = request_approval(caller, context["proposal"]())
    with context["owner"].begin() as connection:
        connection.execute(
            agent_runs.update()
            .where(agent_runs.c.id == run)
            .values(status="paused", end_reason="paused", ended_at=func.now())
        )
        if not pruned:
            connection.execute(
                inputs.insert().values(run_id=run, segment=1, owner_id=caller.user_id, input={})
            )
    app = create_app(context["settings"])
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.headers["Origin"] = "https://testserver"
            login(http, context)
            assert http.post(f"/api/agent/runs/{run}/cancel").status_code == 204
            assert ended(context["owner"], run) == ("cancelled", "cancelled")
            assert read_approval(caller, ApprovalRef(id=approval.id)).status == "rejected"
            # A new turn starts; once it ends, the conversation can be archived.
            data = turn(conversation)
            service.start(caller, data, "fixture-version")
            service.finish(caller, data, "finished", {})
            archived = http.patch(f"/api/conversations/{conversation}", json={"archived": True})
            assert archived.status_code == 200
    finally:
        app.state.engine.dispose()
    with context["owner"].connect() as connection:
        written = connection.execute(select(events.c.event).where(events.c.run_id == run))
        assert list(written.scalars()) == ([] if pruned else [service.CANCELLED])
