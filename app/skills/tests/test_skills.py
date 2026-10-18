"""Real authenticated storage, stable identity, immutable history and request replay."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import UUID, uuid4

import msgspec
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app import create_app
from app.infrastructure.database import metadata as m
from app.infrastructure.operations.registry import registry

ORIGIN = {"Origin": "https://testserver"}


@pytest.fixture
def http(chat):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as client:
        assert chat["sign_in"](client).status_code == 200
        yield client


def definition(**changes):
    return {
        "expectedRevision": 0,
        "mutationId": str(uuid4()),
        "name": "Engineering comparison",
        "description": "Compare generated engineering parameters.",
        "instructions": "Read the selected documents and compare their values.",
        "exampleInputs": ["Compare the two power budgets"],
        "toolNames": ["list_documents", "read_note"],
    } | changes


def save(http, identifier, body):
    return http.put(f"/api/skills/{identifier}", json=body, headers=ORIGIN)


def create(http):
    identifier = uuid4()
    body = definition()
    response = save(http, identifier, body)
    assert response.status_code == 200
    return identifier, body, response.json()


def test_create_list_reopen_on_fresh_client_and_rename_keeps_identity(http, chat):
    identifier, first, saved = create(http)
    assert saved["id"] == str(identifier) and saved["revision"] == 1
    assert saved["instructions"] == first["instructions"]
    assert saved["exampleInputs"] == first["exampleInputs"]
    assert saved["toolNames"] == first["toolNames"] and saved["unavailableTools"] == []
    page = http.get("/api/skills").json()
    assert page["nextCursor"] is None and len(page["items"]) == 1
    assert page["items"][0]["id"] == str(identifier)
    assert "instructions" not in page["items"][0]
    edited = definition(
        expectedRevision=1, name="Renamed comparison", instructions="New instructions"
    )
    second = save(http, identifier, edited)
    assert second.status_code == 200 and second.json()["revision"] == 2
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as fresh:
        assert chat["sign_in"](fresh).status_code == 200
        current = fresh.get(f"/api/skills/{identifier}")
        assert current.status_code == 200
        assert current.json()["name"] == "Renamed comparison"
        assert current.json()["revision"] == 2 and current.json()["id"] == str(identifier)
        old = fresh.get(f"/api/skills/{identifier}/versions?revision=1")
        assert old.status_code == 200 and old.json() == saved
        assert fresh.get(f"/api/skills/{identifier}/versions?revision=3").status_code == 404


def test_exact_retries_return_original_revision_after_later_edits(http, chat):
    identifier, first, original = create(http)
    edit = definition(expectedRevision=1, instructions="Second")
    second = save(http, identifier, edit)
    assert second.status_code == 200
    assert save(http, identifier, first).json() == original
    assert save(http, identifier, edit).json() == second.json()
    assert http.get(f"/api/skills/{identifier}").json()["revision"] == 2
    with chat["owner"].connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(m.skill_revisions)
                .where(m.skill_revisions.c.skill_id == identifier)
            )
            == 2
        )


@pytest.mark.parametrize("change", [{"name": "Other"}, {"expectedRevision": 1}, {"toolNames": []}])
def test_reusing_mutation_with_changed_request_conflicts(http, change):
    identifier, first, _ = create(http)
    assert save(http, identifier, first | change).status_code == 409
    assert http.get(f"/api/skills/{identifier}").json()["revision"] == 1


def test_owner_mutation_cannot_be_reused_for_another_skill(http):
    identifier, first, _ = create(http)
    other = uuid4()
    assert save(http, other, first).status_code == 409
    assert http.get(f"/api/skills/{other}").status_code == 404
    assert http.get(f"/api/skills/{identifier}").json()["revision"] == 1


def test_stale_edit_conflicts_without_replacing_current_or_history(http):
    identifier, _, original = create(http)
    latest = definition(expectedRevision=1, instructions="Latest")
    assert save(http, identifier, latest).status_code == 200
    assert (
        save(http, identifier, definition(expectedRevision=1, instructions="Stale")).status_code
        == 409
    )
    assert http.get(f"/api/skills/{identifier}").json()["instructions"] == "Latest"
    assert http.get(f"/api/skills/{identifier}/versions?revision=1").json() == original


def test_two_concurrent_edits_append_once(http, chat):
    identifier, _, _ = create(http)

    def edit(name):
        with TestClient(create_app(chat["settings"]), base_url="https://testserver") as client:
            assert chat["sign_in"](client).status_code == 200
            return save(client, identifier, definition(expectedRevision=1, name=name)).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, ["A", "B"])) == [200, 409]
    assert http.get(f"/api/skills/{identifier}").json()["revision"] == 2


def test_two_concurrent_identical_creates_replay_once(http, chat):
    identifier, body = uuid4(), definition()

    def put(_):
        with TestClient(create_app(chat["settings"]), base_url="https://testserver") as client:
            assert chat["sign_in"](client).status_code == 200
            return save(client, identifier, body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(put, range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert http.get(f"/api/skills/{identifier}/versions?revision=2").status_code == 404


def test_live_tool_validation_preserves_old_definition_and_replay(http, monkeypatch):
    identifier, body, original = create(http)
    key = next(key for key, value in registry.items() if value.tool_name == "read_note")
    monkeypatch.delitem(registry, key)
    current = http.get(f"/api/skills/{identifier}").json()
    assert current["toolNames"] == original["toolNames"]
    assert current["unavailableTools"] == ["read_note"]
    assert save(http, identifier, body).status_code == 200
    assert save(http, identifier, definition(expectedRevision=1)).status_code == 400
    corrected = save(http, identifier, definition(expectedRevision=1, toolNames=["list_documents"]))
    assert corrected.status_code == 200 and corrected.json()["unavailableTools"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"expectedRevision": -1},
        {"toolNames": ["invented_tool"]},
        {"toolNames": ["list_documents", "list_documents"]},
        {"owner": str(uuid4())},
    ],
)
def test_invalid_definitions_do_not_create_an_anchor(http, chat, changes):
    identifier = uuid4()
    assert save(http, identifier, definition(**changes)).status_code == 400
    with chat["owner"].connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(m.skills).where(m.skills.c.id == identifier)
            )
            == 0
        )


def test_failed_append_rolls_back_new_anchor_and_changed_head(http, chat, monkeypatch):
    from app.skills import service, storage
    from app.skills.handlers import SaveSkill

    identifier, _, _ = create(http)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic append failure")

    monkeypatch.setattr(storage, "append", fail)
    for key, expected in [(identifier, 1), (uuid4(), 0)]:
        request = SaveSkill(
            id=key,
            expected_revision=expected,
            mutation_id=uuid4(),
            name="Synthetic",
            description="",
            instructions="Test",
            example_inputs=[],
            tool_names=[],
        )
        with pytest.raises(RuntimeError, match="synthetic append failure"):
            service.save(chat["caller"], request)
        response = http.get(f"/api/skills/{key}")
        assert response.status_code == (200 if expected else 404)
        if expected:
            assert response.json()["revision"] == 1


def test_unknown_and_other_owner_ids_versions_writes_and_retry_are_hidden(http, chat):
    from app.infrastructure.errors import NotFound
    from app.skills import service
    from app.skills.handlers import SaveSkill

    identifier, first, _ = create(http)
    caller = replace(chat["caller"], user_id=uuid4())
    for key in (identifier, uuid4()):
        with pytest.raises(NotFound):
            service.read(caller, key)
        with pytest.raises(NotFound):
            service.read(caller, key, 1)
    assert service.list_owned(caller, None, 50)["items"] == []
    request = SaveSkill(
        id=identifier,
        expected_revision=1,
        mutation_id=UUID(first["mutationId"]),
        name="Hidden",
        description="",
        instructions="Test",
        example_inputs=[],
        tool_names=[],
    )
    with pytest.raises(NotFound):
        service.save(caller, request)
    request = msgspec.structs.replace(request, expected_revision=0)
    with pytest.raises(NotFound):
        service.save(caller, request)
    assert http.get(f"/api/skills/{identifier}").json()["revision"] == 1


def test_failure_after_append_and_head_change_rolls_back_both(http, chat, monkeypatch):
    from app.skills import service, storage
    from app.skills.handlers import SaveSkill

    identifier, _, _ = create(http)
    original_advance = storage.advance

    def fail_after_advance(*args, **kwargs):
        assert original_advance(*args, **kwargs) is not None
        raise RuntimeError("synthetic post-advance failure")

    monkeypatch.setattr(storage, "advance", fail_after_advance)
    for key, expected in [(identifier, 1), (uuid4(), 0)]:
        request = SaveSkill(
            id=key,
            expected_revision=expected,
            mutation_id=uuid4(),
            name="Rollback",
            description="",
            instructions="Test",
            example_inputs=[],
            tool_names=[],
        )
        with pytest.raises(RuntimeError, match="synthetic post-advance failure"):
            service.save(chat["caller"], request)
        with chat["owner"].connect() as connection:
            assert connection.scalar(select(m.skills.c.revision).where(m.skills.c.id == key)) == (
                1 if expected else None
            )
            assert connection.scalar(
                select(func.count())
                .select_from(m.skill_revisions)
                .where(m.skill_revisions.c.skill_id == key)
            ) == (1 if expected else 0)


def test_anonymous_and_cross_origin_requests_cannot_read_or_write(http, chat):
    identifier, _, _ = create(http)
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as anonymous:
        for path in (
            "/api/skills",
            f"/api/skills/{identifier}",
            f"/api/skills/{identifier}/versions?revision=1",
        ):
            assert anonymous.get(path).status_code == 401
        assert save(anonymous, uuid4(), definition()).status_code == 401
    assert (
        http.put(
            f"/api/skills/{uuid4()}", json=definition(), headers={"Origin": "https://other.example"}
        ).status_code
        == 403
    )
    assert http.put(f"/api/skills/{uuid4()}", json=definition()).status_code == 403


def test_cursor_pages_current_heads_only(http):
    identifiers = [create(http)[0] for _ in range(3)]
    seen, cursor = [], None
    while True:
        response = http.get(
            "/api/skills", params={"limit": 1} | ({"after": str(cursor)} if cursor else {})
        )
        assert response.status_code == 200
        page = response.json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert seen == [str(identifier) for identifier in sorted(identifiers)]
    assert http.get("/api/skills", params={"limit": 0}).status_code == 400
    assert http.get("/api/skills", params={"limit": 101}).status_code == 400


def test_revision_grants_are_append_only_and_owner_pair_is_enforced(http, chat):
    identifier, _, _ = create(http)
    with chat["caller"].sessions() as session:
        for privilege in ("UPDATE", "DELETE"):
            assert not session.scalar(
                text("SELECT has_table_privilege(current_user,'skill_revisions',:privilege)"),
                {"privilege": privilege},
            )
        for privilege in ("SELECT", "INSERT"):
            assert session.scalar(
                text("SELECT has_table_privilege(current_user,'skill_revisions',:privilege)"),
                {"privilege": privilege},
            )
    with chat["caller"].sessions() as session, pytest.raises(DBAPIError):
        session.execute(
            m.skill_revisions.update()
            .where(m.skill_revisions.c.skill_id == identifier)
            .values(instructions="Changed")
        )
    with chat["owner"].connect() as connection:
        transaction = connection.begin()
        try:
            other = connection.execute(
                m.metadata.tables["users"]
                .insert()
                .values(username="skill_" + uuid4().hex, display_name="Synthetic skill account")
                .returning(m.metadata.tables["users"].c.id)
            ).scalar_one()
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    m.skill_revisions.insert().values(
                        skill_id=identifier,
                        owner=other,
                        revision=2,
                        mutation_id=uuid4(),
                        request_hash="0" * 64,
                        name="Wrong",
                        description="",
                        instructions="Wrong",
                        example_inputs=[],
                        tool_names=[],
                    )
                )
        finally:
            transaction.rollback()


def test_catalog_distinguishes_stored_editor_from_unattached_native_skill_execution(http):
    create(http)
    response = http.get("/api/tools/capabilities")
    assert response.status_code == 200
    assert response.json()["skills"] == [] and response.json()["skillsStatus"] == "not_installed"
    from app.agent.fake import catalog_summary

    assert "agent" in catalog_summary(None, "skills").lower()
    assert "edit" in catalog_summary(None, "skills").lower()
