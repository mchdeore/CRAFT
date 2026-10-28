"""Owned durable history and per-answer retrieval records through real HTTP."""

import json
from uuid import uuid4

import msgspec
import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.agent.tests.test_chat_stream import collect, request
from app.tests.test_contract import typescript_schema, validate_chat_events


@pytest.mark.slow
def test_conversations_persist_reload_messages_and_answers(chat):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        created = http.post(
            "/api/conversations",
            json={"title": "Synthetic reload"},
            headers={"Origin": "https://testserver"},
        )
        assert created.status_code == 200
        identifier = created.json()["id"]
    from uuid import UUID

    data = msgspec.structs.replace(request(chat), thread_id=UUID(identifier))
    events = collect(chat["caller"], data)
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        saved = http.get("/api/conversations/" + identifier)
        assert saved.status_code == 200
        payload = saved.json()
        roles = {m["role"] for m in payload["messages"]}
        assert {"user", "assistant", "tool", "activity"}.issubset(roles)
        answer = next(m for m in payload["messages"] if m.get("activityType") == "craft.answer")
        event = next(e for e in events if e.get("activityType") == "craft.answer")
        equal = answer["content"] == event["content"]
        assert equal and validate_chat_events(events)
        import subprocess

        checked = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                (
                    'import {MessageSchema} from "@ag-ui/core"; let text="";'
                    "for await (const part of process.stdin) text+=part;"
                    "process.exit(MessageSchema.array().safeParse(JSON.parse(text)).success?0:1);"
                ),
            ],
            cwd="app/contract",
            input=json.dumps(payload["messages"]),
            text=True,
            capture_output=True,
        )
        assert checked.returncode == 0
        import jsonschema

        validator = jsonschema.Draft7Validator(typescript_schema("Conversation"))
        errors = list(validator.iter_errors(payload))
        valid = not errors
        assert valid


def test_conversations_answer_retrieval_path(chat, capsys):
    first = request(chat)
    events = collect(chat["caller"], first)
    collect(chat["caller"], request(chat))
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        saved = http.get("/api/conversations/" + str(chat["thread"])).json()
    records = [
        m["content"] for m in saved["messages"] if m.get("activityType") == "craft.retrieval"
    ]
    record = next(r for r in records if r["runId"] == str(first.run_id))
    answer = next(e["content"] for e in events if e.get("activityType") == "craft.answer")
    assert record["messageId"] == answer["messageId"]
    assert record["path"][0]["tool"] == "search" and record["path"][0]["arguments"] == {
        "query": "Peak power"
    }
    assert all(step["at"] and step["toolCallId"] for step in record["path"])
    from sqlalchemy import select

    from app.infrastructure.database import metadata as m

    with chat["owner"].connect() as connection:
        run = (
            connection.execute(
                select(m.metadata.tables["agent_runs"]).where(
                    m.metadata.tables["agent_runs"].c.id == first.run_id
                )
            )
            .mappings()
            .one()
        )
    assert (
        record["instructionsVersion"] == run["instructions_version"]
        and record["model"] == run["model"]
    )
    captured = capsys.readouterr()
    safe = "Peak power" not in captured.out + captured.err
    assert safe


def test_conversations_owner_and_titles(chat, foreign_http):
    from dataclasses import replace

    import pytest

    from app.conversations.handlers import ConversationRef, read_conversation
    from app.infrastructure.errors import NotFound

    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        headers = {"Origin": "https://testserver"}
        for title in ["", "x" * 201]:
            assert (
                http.post("/api/conversations", json={"title": title}, headers=headers).status_code
                == 400
            )
            assert (
                http.patch(
                    "/api/conversations/" + str(chat["thread"]),
                    json={"title": title},
                    headers=headers,
                ).status_code
                == 400
            )
        assert http.get("/api/conversations/" + str(uuid4())).status_code == 404
        listed = http.get("/api/conversations").json()
        assert len(listed) <= 100 and any(r["id"] == str(chat["thread"]) for r in listed)
        assert (
            http.patch(
                "/api/conversations/" + str(chat["thread"]),
                json={"title": "Renamed"},
                headers=headers,
            ).status_code
            == 200
        )
        assert http.delete("/api/session", headers=headers).status_code == 204
        assert chat["sign_in"](http).status_code == 200
        assert http.get("/api/conversations/" + str(chat["thread"])).json()["title"] == "Renamed"
    assert foreign_http.get("/api/conversations/" + str(chat["thread"])).status_code == 404
    assert (
        foreign_http.patch(
            "/api/conversations/" + str(chat["thread"]),
            json={"title": "Foreign"},
            headers={"Origin": "https://testserver"},
        ).status_code
        == 404
    )
    assert foreign_http.get("/api/conversations").json() == []
    from app.agent import service

    data = request(chat)
    service.start(chat["caller"], data, "synthetic")
    try:
        with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
            assert chat["sign_in"](http).status_code == 200
            assert (
                http.patch(
                    "/api/conversations/" + str(chat["thread"]),
                    json={"archived": True},
                    headers={"Origin": "https://testserver"},
                ).status_code
                == 409
            )
    finally:
        service.finish(chat["caller"], data, "cancelled", {})
    with pytest.raises(NotFound):
        read_conversation(
            replace(chat["caller"], user_id=uuid4()), ConversationRef(id=chat["thread"])
        )


def test_conversations_group_assignment_and_restore(chat, foreign_http):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        headers = {"Origin": "https://testserver"}
        path = "/api/conversations/" + str(chat["thread"])
        grouped = http.patch(path, json={"groupName": "Planning"}, headers=headers)
        assert grouped.status_code == 200 and grouped.json()["groupName"] == "Planning"
        listed = http.get("/api/conversations").json()
        own = next(row for row in listed if row["id"] == str(chat["thread"]))
        assert own["groupName"] == "Planning"
        assert own["archived"] is False
        cleared = http.patch(path, json={"groupName": None}, headers=headers)
        assert cleared.status_code == 200 and cleared.json()["groupName"] is None
        archived = http.patch(path, json={"archived": True}, headers=headers)
        assert archived.status_code == 200 and archived.json()["archived"] is True
        assert http.get(path).status_code == 200
        restored = http.patch(path, json={"archived": False}, headers=headers)
        assert restored.status_code == 200 and restored.json()["archived"] is False
        assert http.get(path).status_code == 200
        created = http.post(
            "/api/conversations",
            json={"title": "Grouped from create", "groupName": "Inbox"},
            headers=headers,
        )
        assert created.status_code == 200 and created.json()["groupName"] == "Inbox"
        for group in ["", "   ", "x" * 81]:
            assert http.patch(path, json={"groupName": group}, headers=headers).status_code == 400
    assert (
        foreign_http.patch(
            "/api/conversations/" + str(chat["thread"]),
            json={"groupName": "Foreign"},
            headers={"Origin": "https://testserver"},
        ).status_code
        == 404
    )


def test_french_reply_carries_english_originals(chat):
    from app.accounts.handlers import MeChange, change_me
    from app.agent.runtime import language_instructions

    assert "Reply language for this run: French." in language_instructions("fr")
    change_me(chat["caller"], MeChange(language="fr"))
    try:
        french = request(chat)
        events = collect(chat["caller"], french)
    finally:
        change_me(chat["caller"], MeChange(language="en"))
    answer = next(e["content"] for e in events if e.get("activityType") == "craft.answer")
    shown = next(e["content"] for e in events if e.get("activityType") == "craft.originals")
    assert validate_chat_events(events) and shown["messageId"] == answer["messageId"]
    assert shown["items"] == [
        {"handle": c["handle"], "language": "en", "text": c["quote"]} for c in answer["citations"]
    ]
    english = collect(chat["caller"], request(chat))
    assert not any(e.get("activityType") == "craft.originals" for e in english)
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        saved = http.get("/api/conversations/" + str(chat["thread"])).json()["messages"]
    assert [m["content"] for m in saved if m.get("activityType") == "craft.originals"] == [shown]
    records = {
        m["content"]["runId"]: m["content"]
        for m in saved
        if m.get("activityType") == "craft.retrieval"
    }
    record = records[str(french.run_id)]
    assert record["replyLanguage"] == "fr" and record["originals"] == len(shown["items"])
