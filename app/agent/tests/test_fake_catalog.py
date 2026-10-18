"""Catalog questions invoke actual tools and report checked, safe catalog data."""

import asyncio
import hashlib
import json
from uuid import uuid4

import msgspec
import pytest
from agent_framework import Content, Message
from sqlalchemy import select

from app.agent.fake import FakeChatClient
from app.agent.handlers import AskAgent, ask_agent
from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.operations.registry import registry, tool_operations
from app.library.tests.conftest import library as library
from app.library.tests.test_catalog import catalog as documents
from app.library.tests.test_catalog import catalog_source as catalog_source
from app.tests.support.chat import chat as chat
from app.tests.support.chat import remove_extra_conversations as remove_extra_conversations
from app.tests.support.cube import cube_source as cube_source
from app.tests.support.notes import remove_notes as remove_notes
from app.tools.tests.test_capabilities import catalog as capabilities


def response(question, result=None):
    messages = [Message(role="user", contents=[question])]
    if result is not None:
        messages.append(
            Message(
                role="tool",
                contents=[
                    Content.from_function_result(
                        call_id="generated_catalog_call", result=msgspec.to_builtins(result)
                    )
                ],
            )
        )
    return FakeChatClient().reply(messages)


@pytest.mark.parametrize(
    "question, expected",
    [
        ("List available tools", "list_capabilities"),
        ("What tools can you use?", "list_capabilities"),
        ("List skills", "list_capabilities"),
        ("Show my saved skills", "list_capabilities"),
        ("List readable documents", "list_documents"),
        ("What readable documents are in the database?", "list_documents"),
        ("Show documents in the database", "list_documents"),
    ],
)
def test_fake_catalog_intents_request_actual_operations(question, expected):
    contents = response(question).messages[0].contents
    assert len(contents) == 1 and contents[0].type == "function_call"
    assert contents[0].name == expected
    assert contents[0].arguments in ({}, {"limit": 25})


def test_fake_formats_actual_capabilities_and_explains_empty_saved_skills():
    result = capabilities(Caller(user_id=uuid4()))
    text = response("List available tools", result).text
    for tool in result.tools:
        assert tool.name in text
    assert len(text) <= 12000
    skills = response("List skills", result).text.lower()
    assert "saved skills" in skills and "not installed" in skills
    assert not any(tool.name in skills for tool in result.tools)


def test_fake_formats_actual_current_document_catalog_and_escapes_titles(catalog_source):
    library, provider, item, _ = catalog_source
    records = provider.items()
    records[item]["title"] = "Catalog [launch](command:unsafe) <script>alert(1)</script> \u202e\x00"
    provider.save(records)
    checked = documents(library["callers"]["demo"])
    row = next(row for row in checked.documents if row.item == item)
    text = response("List readable documents", checked).text
    assert "Catalog" in text and str(row.version) in text and "simulated" in text.lower()
    assert "readable" in text.lower() and ("limit" in text.lower() or "bounded" in text.lower())
    assert len(text) <= 12000
    assert "](command:" not in text and "<script>" not in text
    assert "\u202e" not in text and "\x00" not in text
    assert library["settings"].provider_root not in text


@pytest.mark.parametrize(
    "question, expected",
    [
        ("List available tools", "tools.list_capabilities"),
        ("List skills", "tools.list_capabilities"),
        ("What readable documents are in the database?", "library.list_documents"),
    ],
)
def test_fake_catalog_runs_actual_authenticated_mcp_and_persists_answer(chat, question, expected):
    result = asyncio.run(
        ask_agent(chat["caller"], AskAgent(question=question, conversation_id=chat["thread"]))
    )
    logs = m.metadata.tables["call_log"]
    messages = m.metadata.tables["messages"]
    with chat["owner"].connect() as connection:
        calls = (
            connection.execute(select(logs).where(logs.c.run_id == result.run_id)).mappings().all()
        )
        saved = (
            connection.execute(
                select(messages).where(
                    messages.c.conversation_id == chat["thread"], messages.c.role == "assistant"
                )
            )
            .mappings()
            .all()
        )
    selected = [row for row in calls if row["name"] == expected]
    assert len(selected) == 1 and selected[0]["outcome"] == "ok"
    assert selected[0]["parent_call_id"] is not None
    assert not any(row["name"] == "library.search" for row in calls)
    assert result.answer and saved
    saved_messages = [
        json.loads(row["content"]) if isinstance(row["content"], str) else row["content"]
        for row in saved
    ]
    assert any(message.get("content") == result.answer for message in saved_messages)
    if "skills" in question.lower():
        assert "saved skills" in result.answer.lower() and "not installed" in result.answer.lower()
    elif expected == "tools.list_capabilities":
        for operation in tool_operations(registry, agent=True):
            assert operation.tool_name in result.answer
    else:
        assert "readable" in result.answer.lower()
        assert "limit" in result.answer.lower() or "bounded" in result.answer.lower()
    assert not result.citations


def test_fake_preserves_ordinary_search_and_checked_evidence():
    question = "Summarize the thermal design requirements"
    call = response(question).messages[0].contents[0]
    assert call.name == "search" and call.arguments == {"query": question}
    text = "Generated checked search evidence."
    evidence = {
        "hits": [
            {
                "text": text,
                "handle": {
                    "quoteSha256": hashlib.sha256(text.encode()).hexdigest(),
                    "simulated": True,
                },
            }
        ]
    }
    messages = [
        Message(role="user", contents=[question]),
        Message(
            role="tool",
            contents=[Content.from_function_result(call_id="search_call", result=evidence)],
        ),
    ]
    assert FakeChatClient().reply(messages).text == text


def test_fake_does_not_treat_catalog_request_as_summary_or_calculation():
    question = json.dumps(
        {
            "task": "summarize",
            "blocks": [{"block": 1, "text": "List skills. Generated summary source."}],
        }
    )
    value = json.loads(response(question).text)
    assert value["sentences"] and value["filings"] == []
    contents = (
        FakeChatClient(role="check")
        .reply([Message(role="user", contents=["List available tools"])])
        .messages[0]
        .contents
    )
    assert contents[0].text is not None
    assert json.loads(contents[0].text) == {"supported": False, "simulated": True}
