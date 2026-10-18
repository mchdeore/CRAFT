"""Owned synthetic conversations shared by streaming and transport tests."""

from dataclasses import replace

import pytest
from sqlalchemy import select

from app.infrastructure.database import metadata as m


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
            yield source | {
                "thread": identifier,
                "settings": configured,
                "caller": replace(source["caller"], settings=configured),
            }
    else:
        yield source | {"thread": identifier}
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
