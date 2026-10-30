"""Outside asks queue the cited runtime and read its answer later; nested asks stay unavailable."""

import asyncio
from uuid import uuid4

from agent_framework import ChatResponse, Content, Message
from sqlalchemy import select

from app.infrastructure.database import metadata as m
from app.infrastructure.mcp.client import client
from app.infrastructure.operations.registry import registry
from app.library.handlers import CitationCheck, check_citation
from app.tests.support.chat import ask, wait_for
from app.tests.support.chat import chat as chat
from app.tests.support.cube import cube_source as cube_source
from app.tests.support.model import ScriptedChatClient


def test_ask_agent_answer_is_cited(chat):
    result = ask(chat["caller"], "Peak power", chat["thread"])
    assert result.answer and result.citations
    for citation in result.citations:
        assert (
            check_citation(chat["caller"], CitationCheck(handle=citation.handle)).status
            == "current"
        )
    logs = m.metadata.tables["call_log"]

    def logged():
        with chat["owner"].connect() as connection:
            return (
                connection.execute(select(logs).where(logs.c.run_id == result.run_id))
                .mappings()
                .all()
            )

    # The worker logs its own operation after it has written RUN_FINISHED.
    wait_for(lambda: any(row["name"] == "agent.agent_run" for row in logged()))
    rows = logged()
    # The ask is logged with its run; the worker's run and its tool calls nest under it.
    root = next(row for row in rows if row["name"] == "agent.ask_agent")
    worker = next(row for row in rows if row["name"] == "agent.agent_run")
    tool = next(row for row in rows if row["name"] == "library.search")
    assert root["conversation_id"] == chat["thread"]
    assert (
        worker["parent_call_id"] == root["call_id"]
        and tool["parent_call_id"] == worker["call_id"]
        and tool["tool_call_id"]
    )


def test_agent_not_offered_ask_agent(chat):
    from dataclasses import replace

    from app.agent import service
    from app.agent.handlers import RunRef, issue_run_token, revoke_run_token
    from app.tests.support.chat import request

    data = request(chat)
    service.start(chat["caller"], data, "synthetic")
    trusted = replace(chat["caller"], conversation_id=data.thread_id, run_id=data.run_id)
    raw = issue_run_token(trusted, RunRef(run=data.run_id)).token

    async def execute():
        selected = client(chat["settings"].mcp_url, raw, [])
        try:
            await selected.connect()
            names = {function.name for function in selected.functions}
            assert names == {
                op.tool_name
                for op in registry.values()
                if op.tool and op.effects == "NONE" and op.tool_name != "ask_agent"
            }
        finally:
            await selected.close()
        from app.tests.support.mcp import session_stream

        async with session_stream(chat["settings"].mcp_url, raw) as session:
            refused = await session.call_tool("ask_agent", {"question": "Nested run"})
            assert refused.isError and refused.structuredContent is not None
            assert refused.structuredContent["code"] == "forbidden"

    try:
        asyncio.run(execute())
    finally:
        revoke_run_token(trusted, RunRef(run=data.run_id))
        service.finish(trusted, data, "cancelled", {})


def test_read_only_query_cannot_write_notes(chat, monkeypatch):
    original = ScriptedChatClient.reply
    title = "Run note " + uuid4().hex

    def scripted(self, messages):
        newest = max(
            (i for i, message in enumerate(messages) if message.role == "user"), default=-1
        )
        results = [
            c
            for message in messages[newest + 1 :]
            for c in message.contents
            if c.type == "function_result"
        ]
        if not results:
            return ChatResponse(
                messages=[
                    Message(
                        role="assistant",
                        contents=[
                            Content.from_function_call(
                                call_id="note_call",
                                name="write_note",
                                arguments={"title": title, "body": "Synthetic working note"},
                            )
                        ],
                        message_id=uuid4().hex,
                    )
                ],
                response_id=uuid4().hex,
                model="scripted",
            )
        return original(self, messages)

    monkeypatch.setattr(ScriptedChatClient, "reply", scripted)
    result = ask(chat["caller"], "Make a working note", chat["thread"])
    assert result.run_id
    with chat["owner"].connect() as connection:
        rows = connection.execute(select(m.notes.c.owner).where(m.notes.c.title == title)).all()
    assert rows == []
