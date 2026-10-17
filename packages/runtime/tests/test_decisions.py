"""Frozen writes, human-only decisions and one attempted execution."""

import asyncio
from dataclasses import replace

import pytest

from craft_runtime.decisions import Actor, DecisionError, MemoryDecisionGate, Scope

SCOPE = Scope(
    owner="synthetic-owner", run="synthetic-run", operation="set_values", destination="local-values"
)
HUMAN = Actor(user=SCOPE.owner, origin="http", kind="human")
AGENT = Actor(user=SCOPE.owner, origin="mcp", kind="agent")


def test_pause_reject_and_reload_write_nothing():
    async def run():
        gate = MemoryDecisionGate()
        request = await gate.propose(SCOPE, {"value": 12})
        restored = MemoryDecisionGate.restore(gate.snapshot())
        assert restored.status(request.id) == "requested"
        await restored.decide(HUMAN, request.id, approved=False)
        called = []

        async def write(arguments):
            called.append(arguments)

        with pytest.raises(DecisionError, match="not_approved"):
            await restored.execute(AGENT, request.id, SCOPE, {"value": 12}, write)
        assert not called and restored.status(request.id) == "rejected"

    asyncio.run(run())


@pytest.mark.parametrize(
    "actor",
    [
        AGENT,
        replace(HUMAN, user="foreign-owner"),
        replace(HUMAN, origin="python"),
        replace(HUMAN, kind="agent"),
    ],
)
def test_only_requester_human_http_decision(actor):
    async def run():
        gate = MemoryDecisionGate()
        request = await gate.propose(SCOPE, {"value": 12})
        with pytest.raises(DecisionError, match="decision_forbidden"):
            await gate.decide(actor, request.id, approved=True)
        assert gate.status(request.id) == "requested"

    asyncio.run(run())


@pytest.mark.parametrize("field", ["owner", "run", "operation", "destination", "arguments"])
def test_changed_approved_request_is_refused(field):
    async def run():
        gate = MemoryDecisionGate()
        request = await gate.propose(SCOPE, {"value": 12})
        await gate.decide(HUMAN, request.id, approved=True)
        scope = replace(SCOPE, **{field: "changed"}) if field != "arguments" else SCOPE
        arguments = {"value": 13} if field == "arguments" else {"value": 12}
        called = []

        async def write(arguments):
            called.append(arguments)

        with pytest.raises(DecisionError, match="request_changed"):
            await gate.execute(AGENT, request.id, scope, arguments, write)
        assert not called and gate.status(request.id) == "approved"

    asyncio.run(run())


def test_approve_reload_and_concurrent_resume_execute_once():
    async def run():
        gate = MemoryDecisionGate()
        request = await gate.propose(SCOPE, {"value": 12})
        await gate.decide(HUMAN, request.id, approved=True)
        gate = MemoryDecisionGate.restore(gate.snapshot())
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def write(arguments):
            calls.append(arguments)
            entered.set()
            await release.wait()
            return {"written": 1}

        first = asyncio.create_task(gate.execute(AGENT, request.id, SCOPE, {"value": 12}, write))
        await entered.wait()
        with pytest.raises(DecisionError, match="not_approved"):
            await gate.execute(AGENT, request.id, SCOPE, {"value": 12}, write)
        release.set()
        assert await first == {"written": 1}
        assert calls == [{"value": 12}] and gate.status(request.id) == "executed"
        with pytest.raises(DecisionError, match="already_decided"):
            await gate.decide(HUMAN, request.id, approved=True)

    asyncio.run(run())


def test_failed_atomic_batch_has_no_rows_and_cannot_replay():
    async def run():
        gate = MemoryDecisionGate()
        request = await gate.propose(SCOPE, {"values": [12, -1]})
        await gate.decide(HUMAN, request.id, approved=True)
        rows = [5]

        async def atomic_write(arguments):
            staged = list(rows)
            for value in arguments["values"]:
                if value < 0:
                    raise ValueError("synthetic_batch_failure")
                staged.append(value)
            rows[:] = staged

        with pytest.raises(ValueError, match="synthetic_batch_failure"):
            await gate.execute(AGENT, request.id, SCOPE, {"values": [12, -1]}, atomic_write)
        assert rows == [5] and gate.status(request.id) == "failed"
        with pytest.raises(DecisionError, match="not_approved"):
            await gate.execute(AGENT, request.id, SCOPE, {"values": [12, -1]}, atomic_write)

    asyncio.run(run())


def test_cancelled_or_interrupted_attempt_is_never_automatically_replayed():
    async def run():
        gate = MemoryDecisionGate()
        request = await gate.propose(SCOPE, {"value": 12})
        await gate.decide(HUMAN, request.id, approved=True)
        entered = asyncio.Event()

        async def waiting_write(arguments):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            gate.execute(AGENT, request.id, SCOPE, {"value": 12}, waiting_write)
        )
        await entered.wait()
        restored = MemoryDecisionGate.restore(gate.snapshot())
        assert restored.status(request.id) == "uncertain"
        with pytest.raises(DecisionError, match="not_approved"):
            await restored.execute(AGENT, request.id, SCOPE, {"value": 12}, waiting_write)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert gate.status(request.id) == "failed"

    asyncio.run(run())
