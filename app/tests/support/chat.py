"""Owned synthetic conversations shared by streaming and transport tests."""

from contextlib import ExitStack, contextmanager
from dataclasses import replace

import pytest
from sqlalchemy import select, text

from app.infrastructure.database import metadata as m


class AgentWorker:
    """Chat turns run on an in-process agent worker with this test's settings."""

    def __init__(self, owner, settings):
        self.owner, self.settings = owner, settings
        self.stack = ExitStack()

    def start(self, settings):
        from app.infrastructure.queue import worker_thread

        with self.owner.begin() as connection:
            # A job left queued by an earlier test would run with these settings.
            connection.execute(
                text("update procrastinate_jobs set status = 'cancelled' where status = 'todo'")
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
    source = cube_source
    table = m.metadata.tables["conversations"]
    with source["owner"].begin() as connection:
        identifier = connection.execute(
            table.insert()
            .values(
                owner_id=source["caller"].user_id,
                title="Simulated conversation",
            )
            .returning(table.c.id)
        ).scalar_one()
    if getattr(source["settings"], "mcp_url", None):
        from app.infrastructure.mcp.tests.http_fixture import live_server

        with live_server(source["app"]):
            configured = source["app"].state.settings
            with agent_worker(source["owner"], configured) as worker:
                yield source | {
                    "thread": identifier,
                    "settings": configured,
                    "caller": replace(source["caller"], settings=configured),
                    "worker": worker,
                }
    else:
        with agent_worker(source["owner"], source["settings"]) as worker:
            yield source | {"thread": identifier, "worker": worker}
    with source["owner"].begin() as connection:
        connection.execute(
            m.metadata.tables["messages"]
            .delete()
            .where(m.metadata.tables["messages"].c.conversation_id == identifier)
        )
        connection.execute(
            m.metadata.tables["agent_sessions"]
            .delete()
            .where(m.metadata.tables["agent_sessions"].c.conversation_id == identifier)
        )
        connection.execute(
            m.metadata.tables["agent_runs"]
            .delete()
            .where(m.metadata.tables["agent_runs"].c.conversation_id == identifier)
        )
        connection.execute(table.delete().where(table.c.id == identifier))


@pytest.fixture(autouse=True)
def remove_extra_conversations(chat):
    yield
    table = m.metadata.tables["conversations"]
    with chat["owner"].begin() as connection:
        ids = list(
            connection.execute(
                select(table.c.id).where(table.c.owner_id == chat["caller"].user_id)
            ).scalars()
        )
        for name in ["workspace_files", "messages", "agent_sessions", "agent_runs"]:
            owned = m.metadata.tables[name]
            connection.execute(owned.delete().where(owned.c.conversation_id.in_(ids)))
        connection.execute(table.delete().where(table.c.id.in_(ids)))
