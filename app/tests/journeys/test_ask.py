"""Ask in a new conversation and get an answer that cites the library, read it back, then
rename, archive and restore the conversation; a stranger can't see or change any of it."""

import hashlib

import pytest

from app.infrastructure.database import metadata as m
from app.tests.journeys.support import run_body, serve_runs
from app.tests.support.cube import cube_source as cube_source

# Every word of it is in the budget document, so the library search finds the table.
QUESTION = "What is the peak power?"


@pytest.fixture
def library(journey, cube_source):
    """cube_source's budget document, and chat turns whose tools read its library."""
    with serve_runs(journey.owner, cube_source["settings"]):
        yield cube_source


def test_a_person_gets_a_cited_answer_and_renames_archives_and_restores_it(
    journey, library
) -> None:
    http = journey.http
    with journey.owner.begin() as connection:  # their grant lets them read the budget document
        connection.execute(
            m.mission_grants.insert().values(
                user_id=journey.person.id, mission_key=library["mission"], silo=None
            )
        )
    assert http.get("/api/conversations").status_code == 401
    journey.sign_in()

    created = http.post("/api/conversations", json={"title": "Peak power question"})
    assert created.status_code == 200
    conversation = created.json()
    assert (conversation["title"], conversation["archived"]) == ("Peak power question", False)
    thread, path = conversation["id"], f"/api/conversations/{conversation['id']}"

    # The answer quotes the budget table and cites it.
    run, events = journey.chat(thread, QUESTION)
    assert events[0] == {"type": "RUN_STARTED", "threadId": thread, "runId": run}
    assert events[-1] == {"type": "RUN_FINISHED", "threadId": thread, "runId": run}
    answer = next(
        event["content"] for event in events if event.get("activityType") == "craft.answer"
    )
    text = "".join(
        event["delta"]
        for event in events
        if event["type"] == "TEXT_MESSAGE_CONTENT" and event["messageId"] == answer["messageId"]
    )
    [cited] = answer["citations"]
    assert (cited["handle"]["item"], cited["title"], cited["headingPath"]) == (
        library["item"],
        "budget",
        "1 Budget",
    )
    assert "| Peak power | 400 | W |" in cited["quote"] and cited["quote"] in text
    assert hashlib.sha256(cited["quote"].encode()).hexdigest() == cited["handle"]["quoteSha256"]

    # Reloaded, the conversation holds the question, the search and the cited answer, under the
    # ids the stream gave them.
    history = http.get(path)
    assert history.status_code == 200
    messages = history.json()["messages"]
    assert [(message["role"], message.get("activityType")) for message in messages] == [
        ("user", None),
        ("assistant", None),
        ("tool", None),
        ("assistant", None),
        ("activity", "craft.answer"),
        ("activity", "craft.retrieval"),
    ]
    assert messages[0]["content"] == QUESTION
    assert messages[3] == {"id": answer["messageId"], "role": "assistant", "content": text}
    assert messages[4]["content"] == answer and messages[5]["content"]["runId"] == run
    snapshot = next(event["messages"] for event in events if event["type"] == "MESSAGES_SNAPSHOT")
    assert [message["id"] for message in snapshot] == [message["id"] for message in messages]

    # The run is over: a lost stream replays in full, and there is nothing left to stop.
    recovery = http.get(path + "/run")
    assert recovery.status_code == 200
    state = recovery.json()
    assert (state["status"], state["runId"], state["canResume"], state["lastEvent"]) == (
        "finished",
        run,
        False,
        len(events),
    )
    replay = http.get(f"/api/agent/runs/{run}/events", params={"after": 0})
    assert journey.events(replay) == events
    # A page that re-attaches where the stream ended hears at once that nothing follows.
    ended = http.get(f"/api/agent/runs/{run}/events", params={"after": state["lastEvent"]})
    assert (ended.status_code, ended.text) == (200, "")
    assert http.post(f"/api/agent/runs/{run}/cancel").status_code == 409

    renamed = http.patch(path, json={"title": "Peak power, answered"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Peak power, answered"

    # Archived, it stays listed and readable but takes no more questions.
    archived = http.patch(path, json={"archived": True})
    assert archived.status_code == 200
    assert (archived.json()["title"], archived.json()["archived"]) == ("Peak power, answered", True)
    listed = http.get("/api/conversations")
    assert listed.status_code == 200 and listed.json() == [archived.json()]
    assert http.get(path).json()["messages"] == messages
    assert http.get(path + "/run").status_code == 404
    assert http.post("/api/agent/runs", json=run_body(thread, QUESTION)).status_code == 404

    # Restored, it takes questions again.
    restored = http.patch(path, json={"archived": False})
    assert restored.status_code == 200 and restored.json()["archived"] is False
    _, again = journey.chat(thread, QUESTION)
    assert again[-1]["type"] == "RUN_FINISHED"

    # A second person finds none of it, and their attempts leave it as it was.
    kept = http.get(path).json()
    journey.as_stranger(
        ("GET", path),
        ("PATCH", path, {"json": {"title": "Stolen", "archived": True}}),
        ("GET", path + "/run"),
        ("GET", path + "/files"),
        ("POST", "/api/agent/runs", {"json": run_body(thread, QUESTION)}),
        ("GET", f"/api/agent/runs/{run}/events"),
        ("POST", f"/api/agent/runs/{run}/cancel"),
    )
    assert http.get(path).json() == kept
