"""Owned synthetic conversations shared by streaming and transport tests."""

import asyncio
import time
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from uuid import uuid4

import msgspec
import pytest
from sqlalchemy import select, text

from app.agent.handlers import AskAgent, RunAgentInput, ask_agent, get_answer, run_agent
from app.infrastructure.database import metadata as m
from app.infrastructure.mcp.tests.http_fixture import live_server
from app.infrastructure.queue import worker_thread
from app.jobs.handlers import JobRef


def ask(caller, question, conversation=None, seconds=60):
    """Ask through ask_agent, then poll get_answer until the worker's cited answer is ready."""
    queued = asyncio.run(
        ask_agent(caller, AskAgent(question=question, conversation_id=conversation))
    )
    deadline = time.monotonic() + seconds
    while (answer := get_answer(caller, JobRef(job_id=queued.job_id))).status == "pending":
        assert time.monotonic() < deadline, "no answer in time"
        time.sleep(0.05)
    return answer.result


class AgentWorker:
    """Chat turns run on an in-process agent worker with this test's settings."""

    def __init__(self, owner, settings):
        self.owner, self.settings = owner, settings
        self.stack = ExitStack()

    def start(self, settings):
        with self.owner.begin() as connection:
            # An agent job left queued by an earlier test would run with these settings.
            connection.execute(
                text(
                    "update procrastinate_jobs set status = 'cancelled'"
                    " where status = 'todo' and queue_name = 'agent'"
                )
            )
        self.stack.enter_context(worker_thread(settings, ["agent"]))

    def stop(self):
        self.stack.close()

    @contextmanager
    def using(self, settings):
        """Run turns with other settings (the worker's settings, not the caller's, apply)."""
        self.stop()
        self.start(settings)
        try:
            yield
        finally:
            self.stop()
            self.start(self.settings)


@contextmanager
def agent_worker(owner, settings):
    worker = AgentWorker(owner, settings)
    worker.start(settings)
    try:
        yield worker
    finally:
        worker.stop()


@pytest.fixture
def chat(cube_source):
    """A conversation for the cube account, served by a live MCP server and an agent worker.
    cube_source removes what the account wrote."""
    source = cube_source
    owner = source["caller"].user_id
    table = m.metadata.tables["conversations"]
    with source["owner"].begin() as connection:
        identifier = connection.execute(
            table.insert()
            .values(owner_id=owner, title="Simulated conversation")
            .returning(table.c.id)
        ).scalar_one()
    with live_server(source["app"]):
        configured = source["app"].state.settings
        with agent_worker(source["owner"], configured) as worker:
            yield source | {
                "thread": identifier,
                "settings": configured,
                "caller": replace(source["caller"], settings=configured),
                "worker": worker,
            }


def request(chat, content="Peak power", identifier=None):
    return RunAgentInput(
        thread_id=chat["thread"],
        run_id=identifier or uuid4(),
        messages=[{"id": str(uuid4()), "role": "user", "content": content}],
    )


def collect(caller, data):
    async def execute():
        return [msgspec.to_builtins(event) async for event in run_agent(caller, data)]

    return asyncio.run(execute())


def row(chat, identifier):
    with chat["owner"].connect() as connection:
        return (
            connection.execute(
                select(m.metadata.tables["agent_runs"]).where(
                    m.metadata.tables["agent_runs"].c.id == identifier
                )
            )
            .mappings()
            .one()
        )


def wait_for(check, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached")
