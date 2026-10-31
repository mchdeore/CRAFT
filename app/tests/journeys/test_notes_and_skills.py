"""Keep a note with a conversation, rewrite it by its title and delete it; save a skill, send the
save again, edit it, see a stale edit refused and reopen the first revision. A stranger can't
read, change or delete either."""

from datetime import datetime
from uuid import uuid4

# A skill as its editor sends it; each save adds the revision it expects and a mutation id.
SKILL = {
    "name": "Engineering comparison",
    "description": "Compare generated engineering parameters.",
    "instructions": "Read the selected documents and compare their values.",
    "exampleInputs": ["Compare the two power budgets"],
    "toolNames": ["list_documents", "read_note"],
}


def test_a_person_keeps_a_note_with_a_conversation_rewrites_and_deletes_it(journey) -> None:
    http = journey.http
    assert http.get("/api/notes").status_code == 401
    journey.sign_in()

    created = http.post("/api/conversations", json={"title": "Power review"})
    assert created.status_code == 200
    thread = created.json()["id"]

    written = http.put(
        "/api/notes", json={"title": "Power budget", "body": "Peak 350 W", "conversationId": thread}
    )
    assert written.status_code == 200
    note = written.json()
    assert note == {
        "id": note["id"],
        "title": "Power budget",
        "body": "Peak 350 W",
        "conversationId": thread,
        "sizeBytes": 10,
        "createdAt": note["createdAt"],
        "updatedAt": note["createdAt"],
    }
    path = f"/api/notes/{note['id']}"

    # Written again under its title, it is the same note with the new body, still with the
    # conversation.
    body = "Peak 400 W ± 5 %"
    rewritten = http.put("/api/notes", json={"title": "Power budget", "body": body})
    assert rewritten.status_code == 200
    latest = rewritten.json()
    assert latest == note | {
        "body": body,
        "sizeBytes": len(body.encode()),
        "updatedAt": latest["updatedAt"],
    }
    assert datetime.fromisoformat(latest["updatedAt"]) > datetime.fromisoformat(note["updatedAt"])

    # Their notes, and the conversation's, list it without its body; opened, it reads in full.
    summary = {key: value for key, value in latest.items() if key != "body"}
    listed = http.get("/api/notes")
    assert listed.status_code == 200 and listed.json() == [summary]
    in_conversation = http.get("/api/notes", params={"conversationId": thread})
    assert in_conversation.status_code == 200 and in_conversation.json() == [summary]
    opened = http.get(path)
    assert opened.status_code == 200 and opened.json() == latest

    # A second person can't open or delete it, nor list or write notes in the conversation.
    journey.as_stranger(
        ("GET", path),
        ("DELETE", path),
        ("GET", "/api/notes", {"params": {"conversationId": thread}}),
        (
            "PUT",
            "/api/notes",
            {"json": {"title": "Power budget", "body": "", "conversationId": thread}},
        ),
    )
    # Under the same title they keep a note of their own and list only theirs; the person's
    # note is as it was.
    stranger = journey.stranger
    theirs = stranger.put("/api/notes", json={"title": "Power budget", "body": "Peak 900 W"})
    assert theirs.status_code == 200 and theirs.json()["id"] != note["id"]
    assert [row["id"] for row in stranger.get("/api/notes").json()] == [theirs.json()["id"]]
    assert http.get(path).json() == latest
    assert http.get("/api/notes").json() == [summary]

    deleted = http.delete(path)
    assert deleted.status_code == 204 and deleted.content == b""
    assert http.get(path).status_code == 404
    assert http.get("/api/notes").json() == []


def test_a_person_saves_and_edits_a_skill_and_reopens_its_first_revision(journey) -> None:
    http = journey.http
    assert http.get("/api/skills").status_code == 401
    journey.sign_in()
    assert http.get("/api/skills").json() == {"items": [], "nextCursor": None}

    # The editor picks the new skill's id; each save names the revision it expects.
    skill = str(uuid4())
    path = f"/api/skills/{skill}"
    create = SKILL | {"expectedRevision": 0, "mutationId": str(uuid4())}
    created = http.put(path, json=create)
    assert created.status_code == 200
    first = created.json()
    assert first == SKILL | {
        "id": skill,
        "revision": 1,
        "unavailableTools": [],
        "createdAt": first["createdAt"],
        "updatedAt": first["createdAt"],
    }

    # Its answer lost, the browser sends the same save again: the same answer, and no second
    # revision.
    again = http.put(path, json=create)
    assert again.status_code == 200 and again.json() == first
    assert http.get(path).json() == first
    assert http.get(path + "/versions", params={"revision": 2}).status_code == 404

    renamed = {"name": "Peak power comparison", "instructions": "Compare the peaks."}
    edit = SKILL | renamed | {"expectedRevision": 1, "mutationId": str(uuid4())}
    edited = http.put(path, json=edit)
    assert edited.status_code == 200
    second = edited.json()
    assert second == first | renamed | {"revision": 2, "updatedAt": second["updatedAt"]}

    # A second tab, still on revision 1, saves too: refused, and the skill stays as it is.
    other_tab = edit | {"mutationId": str(uuid4()), "instructions": "Compare the averages."}
    stale = http.put(path, json=other_tab)
    assert stale.status_code == 409 and stale.json()["code"] == "conflict"
    assert http.get(path).json() == second
    assert http.get(path + "/versions", params={"revision": 3}).status_code == 404

    # Listed at its latest revision, without the instructions; the first revision still opens.
    listed = http.get("/api/skills")
    assert listed.status_code == 200
    details = {"instructions", "exampleInputs"}
    assert listed.json() == {
        "items": [{key: value for key, value in second.items() if key not in details}],
        "nextCursor": None,
    }
    revision = http.get(path + "/versions", params={"revision": 1})
    assert revision.status_code == 200 and revision.json() == first

    # A second person can't open it or a revision of it, nor edit it at its latest revision.
    # No one deletes a skill: there is no route for it.
    journey.as_stranger(
        ("GET", path),
        ("GET", path + "/versions", {"params": {"revision": 1}}),
        ("PUT", path, {"json": edit | {"expectedRevision": 2, "mutationId": str(uuid4())}}),
    )
    # Nor can they take it by sending the person's first save as their own. Their list stays
    # empty, and the person's skill is as it was.
    journey.as_stranger(("PUT", path, {"json": create}))
    assert journey.stranger.get("/api/skills").json() == {"items": [], "nextCursor": None}
    assert http.get(path).json() == second
    assert http.get(path + "/versions", params={"revision": 3}).status_code == 404
