"""Approve the note the agent asks to write, then reject the same request in a second
conversation: each run waits for its person, a decision is made once, and only the approved note
is written. A stranger can't read or decide either approval, nor stop or resume a waiting run."""

from fastapi.testclient import TestClient

from app.agent import model
from app.tests.journeys.support import run_body
from app.tests.support.approvals import WriteClient

ASK = "Keep the generated calculation."
# What WriteClient asks to write while no tool has answered in the conversation.
NOTE = {"title": "Generated approved scratchpad", "body": "12"}


def new_conversation(http: TestClient, title: str) -> str:
    created = http.post("/api/conversations", json={"title": title})
    assert created.status_code == 200
    return created.json()["id"]


def awaited_approval(events: list[dict]) -> str:
    """The approval a run stopped for: its stream proposes write_note and ends with an interrupt
    for that call, which names the approval."""
    proposed = next(event for event in events if event["type"] == "TOOL_CALL_START")
    assert proposed["toolCallName"] == "write_note"
    end = events[-1]
    assert (end["type"], end["outcome"]["type"]) == ("RUN_FINISHED", "interrupt")
    [interrupt] = end["outcome"]["interrupts"]
    assert (interrupt["reason"], interrupt["toolCallId"]) == ("tool_call", proposed["toolCallId"])
    return interrupt["metadata"]["craft"]["approvalId"]


def run_state(http: TestClient, thread: str) -> dict:
    """The conversation's latest run as a reloaded page reads it, timestamps aside."""
    found = http.get(f"/api/conversations/{thread}/run")
    assert found.status_code == 200
    return {key: value for key, value in found.json().items() if not key.endswith("At")}


def pending_in(http: TestClient, thread: str) -> int:
    """The sidebar's count of approvals the conversation waits on."""
    listed = http.get("/api/conversations")
    assert listed.status_code == 200
    return next(row["pendingApprovals"] for row in listed.json() if row["id"] == thread)


def review(http: TestClient, approval: str) -> dict:
    """What the person reads about an approval: the frozen arguments and what happened since."""
    found = http.get(f"/api/approvals/{approval}")
    assert found.status_code == 200
    return found.json()


def history(detail: dict) -> list[tuple[str, str | None]]:
    """Each event of an approval: what happened, and the person's note with it."""
    return [(event["kind"], event.get("note")) for event in detail["events"]]


def test_a_person_approves_a_proposed_note_and_rejects_another(journey, runs, monkeypatch) -> None:
    # The model asks to write the note until a tool has answered in the conversation, so each
    # approval gets a conversation of its own.
    monkeypatch.setattr(model, "client", lambda settings, role="main": WriteClient())
    http = journey.http
    journey.sign_in()

    # Asked to keep a calculation, the run proposes the note and stops to wait for the person.
    kept = new_conversation(http, "Keep the sum")
    run, events = journey.chat(kept, ASK)
    assert events[0] == {"type": "RUN_STARTED", "threadId": kept, "runId": run}
    approval = awaited_approval(events)

    # A reloaded page finds the run waiting on that approval, and the same stream.
    paused = {
        "status": "paused",
        "runId": run,
        "approvalId": approval,
        "approvalStatus": "pending",
        "canResume": False,
        "lastEvent": len(events),
    }
    assert run_state(http, kept) == paused
    replay = http.get(f"/api/agent/runs/{run}/events", params={"after": 0})
    assert journey.events(replay) == events
    assert pending_in(http, kept) == 1

    # The approvals list shows it, and its detail what would be written; nothing is, yet.
    listed = http.get("/api/approvals", params={"status": "pending"})
    assert listed.status_code == 200
    [summary] = listed.json()
    assert (summary["id"], summary["conversationId"], summary["runId"], summary["tool"]) == (
        approval,
        kept,
        run,
        "write_note",
    )
    detail = review(http, approval)
    assert detail["arguments"] == NOTE | {"conversationId": None}
    assert history(detail) == [("requested", None)]
    assert http.get("/api/notes").json() == []

    # Going on before deciding is refused, and the run still waits with its stream.
    resume = {"approvalId": approval}
    going_on = run_body(kept, "", runId=run, resume=resume)
    assert http.post("/api/agent/runs", json=going_on).status_code == 409
    assert run_state(http, kept) == paused
    assert journey.events(http.get(f"/api/agent/runs/{run}/events")) == events

    # A second person can't read or decide it, nor follow, stop or resume its run.
    journey.as_stranger(
        ("GET", f"/api/approvals/{approval}"),
        ("POST", f"/api/approvals/{approval}/decision", {"json": {"approve": True}}),
        ("GET", f"/api/conversations/{kept}/run"),
        ("GET", f"/api/agent/runs/{run}/events"),
        ("POST", f"/api/agent/runs/{run}/cancel"),
        ("POST", "/api/agent/runs", {"json": going_on}),
    )
    assert journey.stranger.get("/api/approvals").json() == []

    # Approved with a note, it still writes nothing until the person resumes the run.
    decided = http.post(
        f"/api/approvals/{approval}/decision", json={"approve": True, "note": "Checked the sum."}
    )
    assert decided.status_code == 200
    assert decided.json() == summary | {"status": "approved"}
    assert run_state(http, kept) == paused | {"approvalStatus": "approved", "canResume": True}
    assert pending_in(http, kept) == 0
    assert http.get("/api/notes").json() == []

    # Resumed, the run writes the note, once, and finishes.
    _, resumed = journey.chat(kept, "", runId=run, resume=resume)
    assert resumed[-1] == {"type": "RUN_FINISHED", "threadId": kept, "runId": run}
    notes = http.get("/api/notes")
    assert notes.status_code == 200
    [note] = notes.json()
    written = http.get(f"/api/notes/{note['id']}")
    assert written.status_code == 200
    assert (written.json()["title"], written.json()["body"]) == (NOTE["title"], NOTE["body"])
    detail = review(http, approval)
    assert detail["status"] == "executed"
    assert history(detail) == [
        ("requested", None),
        ("approved", "Checked the sum."),
        ("executed", None),
    ]
    assert {event["actorId"] for event in detail["events"]} == {str(journey.person.id)}
    assert run_state(http, kept) == {
        "status": "finished",
        "runId": run,
        "approvalId": None,
        "approvalStatus": None,
        "canResume": False,
        "lastEvent": len(resumed),
    }

    # In a second conversation they reject the same request, and can't take that back.
    dropped = new_conversation(http, "Drop the sum")
    other_run, other_events = journey.chat(dropped, ASK)
    other = awaited_approval(other_events)
    journey.as_stranger(
        ("GET", f"/api/approvals/{other}"),
        ("POST", f"/api/approvals/{other}/decision", {"json": {"approve": True}}),
    )
    rejected = http.post(
        f"/api/approvals/{other}/decision", json={"approve": False, "note": "Not this one."}
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    again = http.post(f"/api/approvals/{other}/decision", json={"approve": True})
    assert again.status_code == 409 and again.json()["code"] == "conflict"
    state = run_state(http, dropped)
    assert (state["approvalStatus"], state["canResume"]) == ("rejected", True)

    # Resumed, the run goes on without the write: the note is as it was.
    _, continued = journey.chat(dropped, "", runId=other_run, resume={"approvalId": other})
    assert continued[-1] == {"type": "RUN_FINISHED", "threadId": dropped, "runId": other_run}
    assert http.get("/api/notes").json() == notes.json()
    assert history(review(http, other)) == [("requested", None), ("rejected", "Not this one.")]

    # Nothing waits any more: one request was carried out, the other turned down.
    listed = http.get("/api/approvals")
    assert listed.status_code == 200
    assert {row["id"]: row["status"] for row in listed.json()} == {
        approval: "executed",
        other: "rejected",
    }
