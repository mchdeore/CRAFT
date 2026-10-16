"""Outside asks share the cited runtime while nested asks remain unavailable."""

import asyncio
from uuid import uuid4

from agent_framework import ChatResponse, Content, Message
from sqlalchemy import select

from app.agent.fake import FakeChatClient
from app.agent.handlers import AskAgent, ask_agent
from app.infrastructure.database import metadata as m
from app.infrastructure.mcp.client import client
from app.infrastructure.operations.registry import registry
from app.library.handlers import CitationCheck, check_citation
from app.tests.support.chat import chat as chat
from app.tests.support.chat import remove_extra_conversations as remove_extra_conversations
from app.tests.support.cube import cube_source as cube_source
from app.tests.support.notes import remove_notes as remove_notes


def test_ask_agent_returns_cited_answer(chat):
    result = asyncio.run(
        ask_agent(chat["caller"], AskAgent(question="Peak power", conversation_id=chat["thread"]))
    )
    assert result.answer and result.citations
    for citation in result.citations:
        assert (
            check_citation(chat["caller"], CitationCheck(handle=citation.handle)).status
            == "current"
        )
    logs = m.metadata.tables["call_log"]
    with chat["owner"].connect() as connection:
        rows = (
            connection.execute(select(logs).where(logs.c.run_id == result.run_id)).mappings().all()
        )
    root = next(row for row in rows if row["name"] == "agent.ask_agent")
    run = next(row for row in rows if row["name"] == "agent.run_agent")
    tool = next(row for row in rows if row["name"] == "library.search")
    assert (
        run["parent_call_id"] == root["call_id"]
        and tool["parent_call_id"] == run["call_id"]
        and tool["tool_call_id"]
    )


def test_agent_not_offered_ask_agent(chat):
    from dataclasses import replace

    from app.agent import service
    from app.agent.handlers import RunRef, issue_run_token, revoke_run_token
    from app.agent.tests.test_chat_stream import request

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
                op.tool_name for op in registry.values() if op.tool and op.tool_name != "ask_agent"
            }
        finally:
            await selected.close()
        from app.infrastructure.mcp.tests.test_mcp import session_stream

        async with session_stream(chat["settings"].mcp_url, raw) as session:
            refused = await session.call_tool("ask_agent", {"question": "Nested run"})
            assert refused.isError and refused.structuredContent is not None
            assert refused.structuredContent["code"] == "forbidden"

    try:
        asyncio.run(execute())
    finally:
        revoke_run_token(trusted, RunRef(run=data.run_id))
        service.finish(trusted, data, "cancelled", {})


def test_agent_uses_notes(chat, monkeypatch):
    original = FakeChatClient.reply
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
                model="fake",
            )
        return original(self, messages)

    monkeypatch.setattr(FakeChatClient, "reply", scripted)
    result = asyncio.run(
        ask_agent(
            chat["caller"], AskAgent(question="Make a working note", conversation_id=chat["thread"])
        )
    )
    assert result.run_id
    with chat["owner"].connect() as connection:
        rows = connection.execute(select(m.notes.c.owner).where(m.notes.c.title == title)).all()
    assert len(rows) == 1 and rows[0].owner == chat["caller"].user_id
