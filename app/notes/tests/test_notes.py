"""Owned scratch notes use identical checked routes and native MCP operations."""

import asyncio
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy import select

from app import create_app
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import NotFound
from app.notes.handlers import NoteRef, delete_note, read_note


def test_notes_write_replace_list_read_delete(chat):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        headers = {"Origin": "https://testserver"}
        first = http.put(
            "/api/notes",
            json={
                "title": "Synthetic memo",
                "body": "First",
                "conversationId": str(chat["thread"]),
            },
            headers=headers,
        )
        assert first.status_code == 200
        identifier = first.json()["id"]
        second = http.put(
            "/api/notes", json={"title": "Synthetic memo", "body": "Second"}, headers=headers
        )
        matched = second.json()["id"] == identifier and second.json()["body"] == "Second"
        assert matched and second.json()["conversationId"] == str(chat["thread"])
        listed = http.get("/api/notes?conversationId=" + str(chat["thread"])).json()
        assert len(listed) == 1 and "body" not in listed[0] and listed[0]["sizeBytes"] == 6
        equal = http.get("/api/notes/" + identifier).json()["body"] == "Second"
        assert equal
        assert http.delete("/api/notes/" + identifier, headers=headers).status_code == 204
        assert http.get("/api/notes/" + identifier).status_code == 404


def test_notes_owner_only(chat):
    from app.notes.handlers import NoteList, WriteNote, list_notes, write_note

    first = asyncio.run(write_note(chat["caller"], WriteNote(title="Owned", body="Synthetic")))
    foreign = replace(chat["caller"], user_id=uuid4())
    assert list_notes(foreign, NoteList()) == []
    with pytest.raises(NotFound):
        read_note(foreign, NoteRef(id=first.id))
    with pytest.raises(NotFound):
        delete_note(foreign, NoteRef(id=first.id))
    with pytest.raises(NotFound):
        asyncio.run(
            write_note(
                chat["caller"], WriteNote(title="Foreign link", body="", conversation_id=uuid4())
            )
        )


def test_notes_limits(chat):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        headers = {"Origin": "https://testserver"}
        for data in [
            {"title": "", "body": ""},
            {"title": "x" * 201, "body": ""},
            {"title": "Size", "body": "x" * 100001},
        ]:
            assert http.put("/api/notes", json=data, headers=headers).status_code == 400
        with chat["owner"].begin() as connection:
            connection.execute(
                m.notes.insert(),
                [
                    {"owner": chat["caller"].user_id, "title": f"quota-{i}", "body": ""}
                    for i in range(500)
                ],
            )
        assert (
            http.put("/api/notes", json={"title": "501", "body": ""}, headers=headers).status_code
            == 409
        )
        assert (
            http.put(
                "/api/notes", json={"title": "quota-0", "body": "replacement"}, headers=headers
            ).status_code
            == 200
        )
        assert len(http.get("/api/notes").json()) == 100


def test_notes_never_logged(chat, capsys):
    from app.notes.handlers import WriteNote, write_note

    marker = uuid4().hex
    note = asyncio.run(write_note(chat["caller"], WriteNote(title=marker, body=marker)))
    read_note(chat["caller"], NoteRef(id=note.id))
    captured = capsys.readouterr()
    logs = m.metadata.tables["call_log"]
    with chat["owner"].connect() as connection:
        rows = (
            connection.execute(
                select(logs).where(
                    logs.c.user_id == chat["caller"].user_id, logs.c.name.like("notes.%")
                )
            )
            .mappings()
            .all()
        )
    safe = marker not in captured.out + captured.err and all(
        marker not in str(v) for row in rows for v in row.values()
    )
    assert safe and len(rows) == 2


@pytest.mark.slow
def test_mcp_reads_owned_notes_but_unapproved_writes_are_refused(chat):
    from app.notes.handlers import WriteNote, write_note

    note = asyncio.run(
        write_note(chat["caller"], WriteNote(title="MCP memo", body="Synthetic body"))
    )
    params = StdioServerParameters(
        command="uv",
        args=["run", "craft", "mcp", "--as", chat["username"]],
        env=os.environ
        | {
            "CRAFT_DATABASE_NAME": chat["settings"].database_name,
            "CRAFT_PROVIDER_ROOT": chat["settings"].provider_root,
            "CRAFT_MCP_URL": chat["settings"].mcp_url,
        },
    )

    async def execute():
        async with (
            stdio_client(params) as (incoming, outgoing),
            ClientSession(incoming, outgoing) as session,
        ):
            await session.initialize()
            result = await session.call_tool(
                "write_note", {"title": "MCP memo", "body": "Synthetic body"}
            )
            assert result.isError
            identifier = str(note.id)
            read = await session.call_tool("read_note", {"id": identifier})
            assert not read.isError and read.structuredContent is not None
            assert read.structuredContent["body"] == "Synthetic body"
            listed = await session.call_tool("list_notes", {})
            assert not listed.isError and listed.structuredContent is not None
            assert len(listed.structuredContent["items"]) == 1
            removed = await session.call_tool("delete_note", {"id": identifier})
            assert removed.isError

    asyncio.run(execute())
    logs = m.metadata.tables["call_log"]
    with chat["owner"].connect() as connection:
        rows = (
            connection.execute(
                select(logs).where(
                    logs.c.user_id == chat["caller"].user_id,
                    logs.c.name.like("notes.%"),
                    logs.c.origin == "mcp",
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 4 and all(row["origin"] == "mcp" for row in rows)


def test_notes_check_conversation_ownership_without_reading_history(chat):
    """An archived owned conversation still takes notes; a missing or foreign one does not.
    The check reads no history, so no nested read_conversation call is logged."""
    from sqlalchemy import func

    from app.notes.handlers import NoteList, WriteNote, list_notes, write_note

    conversations = m.metadata.tables["conversations"]
    with chat["owner"].begin() as connection:
        connection.execute(
            conversations.update()
            .where(conversations.c.id == chat["thread"])
            .values(archived_at=func.now())
        )
    caller, thread = chat["caller"], chat["thread"]
    note = asyncio.run(
        write_note(caller, WriteNote(title="Archived", body="", conversation_id=thread))
    )
    assert [row.id for row in list_notes(caller, NoteList(conversation_id=thread))] == [note.id]
    for denied, conversation in [(caller, uuid4()), (replace(caller, user_id=uuid4()), thread)]:
        with pytest.raises(NotFound):
            list_notes(denied, NoteList(conversation_id=conversation))
    logs = m.metadata.tables["call_log"]
    with chat["owner"].connect() as connection:
        nested = connection.execute(
            select(func.count()).where(
                logs.c.user_id == caller.user_id,
                logs.c.name == "conversations.read_conversation",
            )
        ).scalar_one()
    assert nested == 0
    delete_note(caller, NoteRef(id=note.id))
