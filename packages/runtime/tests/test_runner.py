"""Native framework runs using synthetic tools and server-owned memory state."""

import asyncio
import json
from dataclasses import asdict

import pytest
from runtime_test_support import QUOTE, collect, fixture

from craft_runtime.decisions import Actor, MemoryDecisionGate
from craft_runtime.state import MemoryRunStore, RunRequest, RuntimeErrorCode


def test_native_read_run_returns_citation_order_and_closes_resources():
    async def run():
        runtime, store, _, credentials, models, sources = fixture()
        request = RunRequest("owner", "conversation", "read-1", "Read a synthetic fact")
        events = await collect(runtime, request)
        assert events[0].kind == "started" and events[-1].kind == "finished"
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert events[-1].data["citations"][0]["text"] == QUOTE
        assert store.runs[request.run].status == "finished"
        assert models[0].closed and sources[0].closed and not credentials.active
        assert "ask_agent" not in models[0].offered
        saved = json.dumps(asdict(store.runs[request.run]))
        assert not any(token in saved for token in credentials.issued)

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode,code",
    [
        ("recursion", "tool_refused"),
        ("malformed", "tool_failed"),
        ("repeat", "tool_limit"),
        ("output", "output_limit"),
        ("timeout", "run_timeout"),
    ],
)
def test_failed_runs_close_and_revoke(mode, code):
    async def run():
        runtime, store, _, credentials, models, sources = fixture(
            mode, seconds=0.05, tool_limit=2, max_tokens=32
        )
        events = await collect(
            runtime, RunRequest("owner", "conversation", "failure-1", "Synthetic input")
        )
        assert events[-1].kind == "error" and events[-1].data["code"] == code
        assert store.runs["failure-1"].status == "failed"
        assert (
            sources[0].closed and all(model.closed for model in models) and not credentials.active
        )

    asyncio.run(run())


def test_failed_save_does_not_report_finished_or_leave_credentials():
    class FailedStore(MemoryRunStore):
        async def save(self, state):
            raise ValueError("synthetic-private-payload")

    async def run():
        runtime, store, _, credentials, models, sources = fixture(store=FailedStore())
        events = await collect(
            runtime, RunRequest("owner", "conversation", "failed-save", "Synthetic input")
        )
        assert events[-1].kind == "error" and events[-1].data == {"code": "run_failed"}
        assert store.runs["failed-save"].status == "failed"
        assert models[0].closed and sources[0].closed and not credentials.active

    asyncio.run(run())


def test_replayed_run_and_foreign_conversation_are_refused():
    async def run():
        runtime, _, _, credentials, _, _ = fixture()
        request = RunRequest("owner", "conversation", "read-1", "Synthetic input")
        await collect(runtime, request)
        with pytest.raises(RuntimeErrorCode, match="run_replayed"):
            await collect(runtime, request)
        with pytest.raises(RuntimeErrorCode, match="not_found"):
            await collect(
                runtime, RunRequest("foreign", "conversation", "read-2", "Synthetic input")
            )
        assert not credentials.active

    asyncio.run(run())


def test_disconnect_cancels_producer_and_revokes():
    async def run():
        runtime, store, _, credentials, _, sources = fixture("timeout")
        iterator = runtime.run(RunRequest("owner", "conversation", "cancel-1", "Synthetic input"))
        assert (await anext(iterator)).kind == "started"
        await asyncio.sleep(0)
        await iterator.aclose()
        assert store.runs["cancel-1"].status == "cancelled" and not credentials.active
        assert all(source.closed for source in sources)

    asyncio.run(run())


@pytest.mark.parametrize("approved", [True, False])
def test_native_pause_decision_resume_uses_new_credentials(approved):
    async def run():
        runtime, store, decisions, credentials, models, sources = fixture("write")
        request = RunRequest("owner", "conversation", "write-1", "Store a synthetic value")
        events = await collect(runtime, request)
        assert events[-1].kind == "paused" and not sources[0].writes and not credentials.active
        proposal = events[-1].data["decisions"][0]
        await decisions.decide(Actor("owner", "http", "human"), proposal, approved=approved)
        resumed = await collect(
            runtime, RunRequest("owner", "conversation", "write-1", resume=True)
        )
        assert resumed[-1].kind == "finished"
        assert sum(len(source.writes) for source in sources) == int(approved)
        assert (
            len(credentials.issued) == 2
            and len(set(credentials.issued)) == 2
            and not credentials.active
        )
        assert all(source.closed for source in sources) and all(model.closed for model in models)
        assert store.runs["write-1"].status == "finished"

    asyncio.run(run())


def test_early_resume_preserves_pending_decision():
    async def run():
        runtime, store, decisions, credentials, _, sources = fixture("write")
        request = RunRequest("owner", "conversation", "early-resume", "Synthetic write")
        first = await collect(runtime, request)
        second = await collect(
            runtime, RunRequest("owner", "conversation", request.run, resume=True)
        )
        assert second[-1].kind == "error" and second[-1].data["code"] == "decision_pending"
        assert store.runs[request.run].status == "paused" and not credentials.active
        await decisions.decide(
            Actor("owner", "http", "human"), first[-1].data["decisions"][0], approved=False
        )
        assert (
            await collect(runtime, RunRequest("owner", "conversation", request.run, resume=True))
        )[-1].kind == "finished"
        assert not any(source.writes for source in sources)

    asyncio.run(run())


def test_tool_budget_is_not_reset_by_approval_resume():
    async def run():
        runtime, store, decisions, credentials, _, sources = fixture("readwrite", tool_limit=1)
        request = RunRequest("owner", "conversation", "budget-resume", "Synthetic read then write")
        first = await collect(runtime, request)
        assert first[-1].kind == "paused" and store.runs[request.run].tool_calls == 1
        await decisions.decide(
            Actor("owner", "http", "human"), first[-1].data["decisions"][0], approved=True
        )
        second = await collect(
            runtime, RunRequest("owner", "conversation", request.run, resume=True)
        )
        assert second[-1].kind == "error" and second[-1].data["code"] == "tool_limit"
        assert not any(source.writes for source in sources) and not credentials.active

    asyncio.run(run())


def test_sqlite_run_persistence_does_not_hold_a_tool_transaction(tmp_path):
    from craft_runtime.sqlite_store import SQLiteRunStore

    async def run():
        store = SQLiteRunStore(tmp_path / "runs.sqlite")
        runtime, _, _, credentials, models, sources = fixture(store=store)
        original = runtime.tools_factory
        checks = []

        def tools(request, token):
            source = original(request, token)
            original_function = source.functions[0].func

            async def lookup(query: str):
                with store.connection() as connection:
                    connection.execute("begin immediate")
                    checks.append(True)
                return await original_function(query)

            source.functions[0].func = lookup
            return source

        runtime.tools_factory = tools
        request = RunRequest("owner", "conversation", "sql-read", "Synthetic question")
        events = await collect(runtime, request)
        assert events[-1].kind == "finished" and checks
        reopened = SQLiteRunStore(store.path)
        assert reopened.read(request).status == "finished"
        assert reopened.read(request).messages
        assert models[0].closed and sources[0].closed and not credentials.active
        with pytest.raises(RuntimeErrorCode, match="not_found"):
            reopened.read(RunRequest("foreign", "conversation", request.run))

    asyncio.run(run())


def test_sqlite_pause_reopens_with_new_runtime_and_no_old_token(tmp_path):
    from craft_runtime.sqlite_store import SQLiteRunStore

    async def run():
        store = SQLiteRunStore(tmp_path / "runs.sqlite")
        runtime, _, decisions, credentials, _, sources = fixture("write", store=store)
        request = RunRequest("owner", "conversation", "sql-write", "Synthetic write")
        first = await collect(runtime, request)
        assert first[-1].kind == "paused" and not sources[0].writes and not credentials.active
        restored_decisions = MemoryDecisionGate.restore(decisions.snapshot())
        proposal = first[-1].data["decisions"][0]
        await restored_decisions.decide(Actor("owner", "http", "human"), proposal, approved=True)
        next_runtime, _, _, next_credentials, _, next_sources = fixture(
            "write", store=SQLiteRunStore(store.path)
        )
        next_runtime.decisions = restored_decisions
        second = await collect(
            next_runtime, RunRequest("owner", "conversation", request.run, resume=True)
        )
        assert second[-1].kind == "finished" and next_sources[0].writes == [12]
        assert store.read(request).status == "finished"
        stored = json.dumps(asdict(store.read(request)))
        assert not any(token in stored for token in credentials.issued + next_credentials.issued)
        assert not next_credentials.active

    asyncio.run(run())


@pytest.mark.parametrize("resource", ["model", "tools"])
def test_cleanup_failure_attempts_other_resources_and_has_safe_outcome(resource):
    async def run():
        runtime, store, _, credentials, models, sources = fixture()
        factory = runtime.model_factory if resource == "model" else runtime.tools_factory

        def broken(*args):
            value = factory(*args)
            original = value.close

            async def close():
                await original()
                raise ValueError("synthetic-private-cleanup-payload")

            value.close = close
            return value

        if resource == "model":
            runtime.model_factory = broken
        else:
            runtime.tools_factory = broken
        events = await collect(
            runtime, RunRequest("owner", "conversation", "cleanup-1", "Synthetic question")
        )
        assert events[-1].kind == "error" and events[-1].data == {"code": "cleanup_failed"}
        assert models[0].closed and sources[0].closed and not credentials.active
        assert store.runs["cleanup-1"].status == "failed"

    asyncio.run(run())
