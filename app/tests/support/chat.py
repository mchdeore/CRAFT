"""Owned synthetic conversations shared by streaming and transport tests."""

from contextlib import ExitStack, contextmanager
from dataclasses import replace

import pytest
from sqlalchemy import select, text

from app.infrastructure.database import metadata as m


def ask(caller, question, conversation=None, seconds=60):
    """Ask through ask_agent, then poll get_answer until the worker's cited answer is ready."""
    import asyncio
    import time

    from app.agent.handlers import AskAgent, ask_agent, get_answer
    from app.jobs.handlers import JobRef

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
        from app.infrastructure.queue import worker_thread

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
    Teardown removes the conversations, notes and skills the account wrote."""
    from app.infrastructure.mcp.tests.http_fixture import live_server

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
    remove_owned(source["owner"], owner)


def remove_owned(engine, owner):
    conversations = m.metadata.tables["conversations"]
    owned = select(conversations.c.id).where(conversations.c.owner_id == owner)
    with engine.begin() as connection:
        # Notes point at conversations, and skill revisions at skills.
        connection.execute(m.notes.delete().where(m.notes.c.owner == owner))
        connection.execute(m.skill_revisions.delete().where(m.skill_revisions.c.owner == owner))
        connection.execute(m.skills.delete().where(m.skills.c.owner == owner))
        for name in ["workspace_files", "messages", "agent_sessions", "agent_runs"]:
            table = m.metadata.tables[name]
            connection.execute(table.delete().where(table.c.conversation_id.in_(owned)))
        connection.execute(conversations.delete().where(conversations.c.owner_id == owner))
