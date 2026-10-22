"""Checked MCP skill discovery, native context and immutable approval recovery."""

import asyncio
import json
import re
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import msgspec
import pytest
from agent_framework import ChatResponse, Content, Message
from fastapi import FastAPI
from sqlalchemy import select

from app.agent import model
from app.agent.tests.test_approval_resume import clear_run_approvals, events, runtime_client
from app.approvals.tests.test_decisions import context as context
from app.infrastructure.database.metadata import metadata
from app.infrastructure.errors import ApiError
from app.infrastructure.operations.registry import registry, tool_named
from app.skills import service
from app.skills.handlers import SaveSkill, Skill
from app.tests.support.model import ScriptedChatClient


@pytest.fixture(autouse=True)
def owned_skills(context):
    with context["owner"].begin() as db:
        runs = metadata.tables["agent_runs"]
        db.execute(runs.update().where(runs.c.id == context["run"]).values(status="finished"))
    yield
    with context["owner"].begin() as db:
        owners = [user for user, _ in context["users"]]
        for name in ("skill_revisions", "skills"):
            table = metadata.tables[name]
            db.execute(table.delete().where(table.c.owner.in_(owners)))
        runs = metadata.tables["agent_runs"]
        ids = (
            db.execute(select(runs.c.id).where(runs.c.conversation_id == context["conversation"]))
            .scalars()
            .all()
        )
    for identifier in ids:
        clear_run_approvals(context, identifier)


def save(http, identifier, revision=0, tool="list_parameters"):
    response = http.put(
        "/api/skills/" + str(identifier),
        json={
            "expectedRevision": revision,
            "mutationId": str(uuid4()),
            "name": "Generated engineering workflow",
            "description": "Generated native skill",
            "instructions": "Use " + tool + " and report its actual result.",
            "exampleInputs": ["Run the generated workflow"],
            "toolNames": [tool],
        },
    )
    assert response.status_code == 200
    return response.json()


def run_body(context, skill=None):
    body = {
        "threadId": str(context["conversation"]),
        "runId": str(uuid4()),
        "messages": [{"role": "user", "content": "Run the generated workflow."}],
    }
    if skill is not None:
        body["skill"] = skill
    return body


def pins(context):
    sessions = metadata.tables["agent_sessions"]
    with context["owner"].connect() as db:
        state = db.execute(
            select(sessions.c.state).where(sessions.c.conversation_id == context["conversation"])
        ).scalar_one()
    return state["state"].get("craftSkills", [])


class SkillClient(ScriptedChatClient):
    def __init__(self, mode="selected"):
        super().__init__()
        self.mode, self.definitions, self.calls = mode, [], []
        self.check_connections = lambda: None

    def _inner_get_response(self, *, messages, stream=False, options=None, **kwargs):
        self.check_connections()
        options = options or {}
        prompt = str(options.get("instructions", "")) + "\n" + "\n".join(m.text for m in messages)
        match = re.search(r"<craft_saved_skills>\s*(.*?)\s*</craft_saved_skills>", prompt, re.S)
        self.definitions = json.loads(match.group(1)) if match else []
        return super()._inner_get_response(
            messages=messages, stream=stream, options=options, **kwargs
        )

    def reply(self, messages):
        newest = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
        results = [
            c for m in messages[newest + 1 :] for c in m.contents if c.type == "function_result"
        ]
        if self.mode in ("discovery", "inspect") and len(results) < (
            3 if self.mode == "discovery" else 2
        ):
            if not results:
                name, arguments = "list_skills", {}
            elif len(results) == 1:
                catalog = json.loads(results[0].result)
                item = catalog["items"][0]
                name, arguments = (
                    "read_skill_version",
                    {"id": item["id"], "revision": item["revision"]},
                )
            else:
                definition = json.loads(results[1].result)
                name, arguments = definition["toolNames"][0], {}
        elif not results:
            if self.mode == "plain":
                assert not self.definitions
                name, arguments = "list_parameters", {}
            else:
                assert self.definitions, "Selected definition must reach native context"
                definition = self.definitions[0]
                name = definition["toolNames"][0]
                arguments = (
                    {
                        "title": "Generated revision " + str(definition["revision"]),
                        "body": str(definition["revision"]),
                    }
                    if name == "write_note"
                    else {}
                )
        else:
            return ChatResponse(
                messages=[
                    Message(
                        role="assistant",
                        contents=[Content.from_text("Generated workflow finished.")],
                    )
                ]
            )
        self.calls.append(name)
        return ChatResponse(
            messages=[
                Message(
                    role="assistant",
                    contents=[
                        Content.from_function_call(
                            call_id="generated-" + name, name=name, arguments=arguments
                        )
                    ],
                )
            ]
        )


def test_skill_tools_are_registered_checked_reads():
    for name in ("list_skills", "read_skill_version"):
        operation = tool_named(registry, name)
        assert operation is not None and operation.effects == "NONE"
    assert not registry["skills.save_skill"].tool
    assert not registry["skills.read_skill"].tool


@pytest.mark.slow
def test_selected_revision_and_fresh_client_edit_execute_different_actual_tools(
    context, monkeypatch
):
    selected = []
    monkeypatch.setattr(model, "client", lambda _: selected.append(SkillClient()) or selected[-1])
    identifier = uuid4()
    with runtime_client(context) as http:
        save(http, identifier)
        old = {"id": str(identifier), "revision": 1}
        result = events(http.post("/api/agent/runs", json=run_body(context, old)))
        assert result[-1]["type"] == "RUN_FINISHED"
        assert "list_parameters" in selected[-1].calls
        assert selected[-1].definitions[0]["revision"] == 1
        assert pins(context) == [old]
        save(http, identifier, 1, "list_missions")
    with runtime_client(context) as http:
        new = {"id": str(identifier), "revision": 2}
        result = events(http.post("/api/agent/runs", json=run_body(context, new)))
        assert result[-1]["type"] == "RUN_FINISHED"
        assert "list_missions" in selected[-1].calls
        assert pins(context) == [new]
        result = events(http.post("/api/agent/runs", json=run_body(context, old)))
        assert result[-1]["type"] == "RUN_FINISHED"
        assert "list_parameters" in selected[-1].calls
        history = http.get("/api/conversations/" + str(context["conversation"])).json()["messages"]
        recorded = [
            m["content"]["skills"] for m in history if m.get("activityType") == "craft.skills"
        ]
        assert [old] in recorded and [new] in recorded


def test_automatic_mcp_discovery_reads_and_records_exact_revision(context, monkeypatch):
    selected = SkillClient("discovery")
    monkeypatch.setattr(model, "client", lambda _: selected)
    identifier = uuid4()
    with runtime_client(context) as http:
        save(http, identifier)
        assert isinstance(http.app, FastAPI)
        engine = http.app.state.engine
        selected.check_connections = lambda: (
            None if engine.pool.checkedout() == 0 else pytest.fail("Transaction spans model call")
        )
        response = events(http.post("/api/agent/runs", json=run_body(context)))
        assert response[-1]["type"] == "RUN_FINISHED"
        assert {"list_skills", "read_skill_version", "list_parameters"} <= set(selected.calls)
        assert pins(context) == [{"id": str(identifier), "revision": 1}]
        overview = http.get("/api/tools/capabilities").json()
        assert overview["skillsStatus"] == "installed"
        assert overview["skills"] == ["Generated engineering workflow"]


@pytest.mark.slow
@pytest.mark.parametrize("approve", [True, False])
def test_pause_edit_fresh_app_resume_keeps_revision_and_exact_effect(context, monkeypatch, approve):
    clients = []
    monkeypatch.setattr(model, "client", lambda _: clients.append(SkillClient()) or clients[-1])
    identifier = uuid4()
    old = {"id": str(identifier), "revision": 1}
    with runtime_client(context) as http:
        save(http, identifier, tool="write_note")
        body = run_body(context, old)
        first = events(http.post("/api/agent/runs", json=body))
        assert first[-1]["outcome"]["type"] == "interrupt"
        assert pins(context) == [old]
        history = http.get("/api/conversations/" + str(context["conversation"])).json()["messages"]
        assert any(
            m.get("activityType") == "craft.skills"
            and m["content"] == {"runId": body["runId"], "skills": [old]}
            for m in history
        )
        save(http, identifier, 1, "list_missions")
        approval = http.get("/api/approvals?status=pending").json()[0]
    with runtime_client(context) as http:
        assert (
            http.post(
                "/api/approvals/" + approval["id"] + "/decision", json={"approve": approve}
            ).status_code
            == 200
        )
        resume = {
            "threadId": body["threadId"],
            "runId": body["runId"],
            "messages": [],
            "resume": {"approvalId": approval["id"]},
            "skill": {"id": str(identifier), "revision": 2},
            "state": {"craftSkills": []},
            "context": [{"role": "system", "content": "Replace revision"}],
        }
        second = events(http.post("/api/agent/runs", json=resume))
        assert second[-1]["type"] == "RUN_FINISHED"
        assert clients[-1].definitions[0]["revision"] == 1
        assert pins(context) == [old]
        notes = http.get("/api/notes").json()
        assert len(notes) == int(approve)
        if approve:
            assert http.get("/api/notes/" + notes[0]["id"]).json()["body"] == "1"
        assert http.post("/api/agent/runs", json=resume).status_code in (404, 409)
        assert len(http.get("/api/notes").json()) == int(approve)
        monkeypatch.setattr(model, "client", lambda _: SkillClient("plain"))
        assert (
            events(http.post("/api/agent/runs", json=run_body(context)))[-1]["type"]
            == "RUN_FINISHED"
        )
        assert pins(context) == []
        history = http.get("/api/conversations/" + body["threadId"]).json()["messages"]
        assert any(
            m.get("activityType") == "craft.skills" and m["content"]["runId"] == body["runId"]
            for m in history
        )


@pytest.mark.parametrize(
    "failure", ["missing", "foreign", "unavailable", "unavailable_channel", "invalid_revision"]
)
def test_bad_selection_has_no_run_or_model_call(context, monkeypatch, failure):
    calls = []
    monkeypatch.setattr(model, "client", lambda _: calls.append(True) or SkillClient())
    identifier = uuid4()
    with runtime_client(context) as http:
        save(
            http,
            identifier,
            tool="ask_agent" if failure == "unavailable_channel" else "list_parameters",
        )
        reference = {"id": str(identifier), "revision": 1}
        if failure == "missing":
            reference["id"] = str(uuid4())
        elif failure == "foreign":
            foreign = uuid4()
            service.save(
                replace(context["caller"], user_id=context["users"][1][0]),
                SaveSkill(
                    id=foreign,
                    expected_revision=0,
                    mutation_id=uuid4(),
                    name="Generated foreign",
                    description="",
                    instructions="Use list_parameters.",
                    example_inputs=[],
                    tool_names=["list_parameters"],
                ),
            )
            reference["id"] = str(foreign)
        elif failure == "unavailable":
            monkeypatch.delitem(registry, "cube.list_parameters")
        elif failure == "invalid_revision":
            reference["revision"] = 0
        body = run_body(context, reference)
        response = http.post("/api/agent/runs", json=body)
        assert response.status_code == (
            400
            if failure == "invalid_revision"
            else 409
            if failure in ("unavailable", "unavailable_channel")
            else 404
        )
        assert not calls
        runs = metadata.tables["agent_runs"]
        with context["owner"].connect() as db:
            assert (
                db.execute(select(runs.c.id).where(runs.c.id == UUID(body["runId"]))).first()
                is None
            )


@pytest.mark.parametrize("result_kind", ["failed", "malformed", "mismatched", "unrelated"])
def test_native_completion_only_pins_checked_matching_success(context, result_kind):
    from app.skills.native import SavedSkillsProvider

    identifier = uuid4()
    definition = service.save(
        context["caller"],
        SaveSkill(
            id=identifier,
            expected_revision=0,
            mutation_id=uuid4(),
            name="Generated completion",
            description="",
            instructions="Use list_parameters.",
            example_inputs=[],
            tool_names=["list_parameters"],
        ),
    )
    provider = SavedSkillsProvider([])
    raw = msgspec.json.encode(Skill(**definition)).decode()
    function = "read_skill_version"
    arguments = {"id": str(identifier), "revision": 1}
    if result_kind == "failed":
        raw = '{"code":"not_found","message":"Not found"}'
    elif result_kind == "malformed":
        raw = "not json"
    elif result_kind == "mismatched":
        arguments["id"] = str(uuid4())
    else:
        function = "list_parameters"
    invocation = SimpleNamespace(
        function=SimpleNamespace(name=function),
        arguments=arguments,
        result=[Content.from_text(raw)],
    )
    if result_kind == "mismatched":
        with pytest.raises(ApiError):
            provider.completed(invocation)
    else:
        provider.completed(invocation)
    assert provider.references == []


@pytest.mark.slow
def test_capability_overview_is_owned_bounded_and_complete_discovery_pages(context):
    from app.tools import service as tools_service

    caller = context["caller"]
    for _ in range(102):
        service.save(
            caller,
            SaveSkill(
                id=uuid4(),
                expected_revision=0,
                mutation_id=uuid4(),
                name="Generated owner A",
                description="",
                instructions="Use list_parameters.",
                example_inputs=[],
                tool_names=["list_parameters"],
            ),
        )
    other = replace(caller, user_id=context["users"][1][0])
    service.save(
        other,
        SaveSkill(
            id=uuid4(),
            expected_revision=0,
            mutation_id=uuid4(),
            name="Generated owner B",
            description="",
            instructions="Use list_parameters.",
            example_inputs=[],
            tool_names=["list_parameters"],
        ),
    )
    overview = tools_service.capabilities(caller)
    assert overview.skills_status == "installed"
    assert len(overview.skills) == 100 and set(overview.skills) == {"Generated owner A"}
    assert tools_service.capabilities(other).skills == ["Generated owner B"]
    first = service.list_owned(caller, None, 100)
    assert len(first["items"]) == 100 and first["next_cursor"] is not None
    second = service.list_owned(caller, first["next_cursor"], 100)
    assert len(second["items"]) == 2 and second["next_cursor"] is None


def test_automatic_metadata_read_of_excluded_tool_does_not_select_skill(context, monkeypatch):
    selected = SkillClient("inspect")
    monkeypatch.setattr(model, "client", lambda _: selected)
    identifier = uuid4()
    with runtime_client(context) as http:
        save(http, identifier, tool="ask_agent")
        response = events(http.post("/api/agent/runs", json=run_body(context)))
        assert response[-1]["type"] == "RUN_FINISHED"
        assert {"list_skills", "read_skill_version"} <= set(selected.calls)
        assert pins(context) == []


def test_native_loaded_revision_pairs_deduplicate_without_losing_versions(context):
    from app.skills.native import SavedSkillsProvider

    request = SaveSkill(
        id=uuid4(),
        expected_revision=0,
        mutation_id=uuid4(),
        name="Generated revisions",
        description="",
        instructions="Use list_parameters.",
        example_inputs=[],
        tool_names=["list_parameters"],
    )
    first = Skill(**service.save(context["caller"], request))
    changed = msgspec.structs.replace(
        request,
        expected_revision=1,
        mutation_id=uuid4(),
        instructions="Use list_missions.",
        tool_names=["list_missions"],
    )
    second = Skill(**service.save(context["caller"], changed))
    provider = SavedSkillsProvider([], ["list_parameters", "list_missions"])
    for definition in (first, first, second, first):
        provider.completed(
            SimpleNamespace(
                function=SimpleNamespace(name="read_skill_version"),
                arguments={"id": str(definition.id), "revision": definition.revision},
                result=[Content.from_text(msgspec.json.encode(definition).decode())],
            )
        )
    assert provider.references == [
        {"id": str(first.id), "revision": 1},
        {"id": str(first.id), "revision": 2},
    ]


def test_oversized_recovery_ledger_is_rejected_before_storage_access(context, monkeypatch):
    from app.skills.native import load

    def forbidden(*args):
        pytest.fail("Oversized recovery state must not reach storage")

    monkeypatch.setattr(service, "read", forbidden)
    with pytest.raises(ApiError):
        load(
            context["caller"],
            [{"id": str(uuid4()), "revision": 1} for _ in range(100)],
            {"list_parameters"},
        )


@pytest.mark.slow
def test_native_recovery_load_failure_finishes_run_without_effect(context, monkeypatch):
    from app.infrastructure.errors import Conflict
    from app.skills import native

    models = []
    monkeypatch.setattr(model, "client", lambda _: models.append(SkillClient()) or models[-1])
    with runtime_client(context) as http:
        identifier = uuid4()
        save(http, identifier, tool="write_note")
        body = run_body(context, {"id": str(identifier), "revision": 1})
        assert events(http.post("/api/agent/runs", json=body))[-1]["outcome"]["type"] == "interrupt"
        approval = http.get("/api/approvals?status=pending").json()[0]
    with runtime_client(context) as http:
        assert (
            http.post(
                "/api/approvals/" + approval["id"] + "/decision", json={"approve": True}
            ).status_code
            == 200
        )

        def failed_load(*args):
            raise Conflict()

        monkeypatch.setattr(native, "load", failed_load)
        count = len(models)
        resume = {
            "threadId": body["threadId"],
            "runId": body["runId"],
            "messages": [],
            "resume": {"approvalId": approval["id"]},
        }
        response = events(http.post("/api/agent/runs", json=resume))
        assert response[-1]["type"] == "RUN_ERROR"
        assert len(models) == count
        assert http.get("/api/notes").json() == []
        runs = metadata.tables["agent_runs"]
        with context["owner"].connect() as db:
            assert (
                db.execute(
                    select(runs.c.status).where(runs.c.id == UUID(body["runId"]))
                ).scalar_one()
                == "failed"
            )
            row = db.execute(
                select(runs.c.token_sha256, runs.c.token_expires_at).where(
                    runs.c.id == UUID(body["runId"])
                )
            ).one()
            assert row.token_sha256 is None and row.token_expires_at is None
        assert http.post("/api/agent/runs", json=resume).status_code in (404, 409)


def test_selected_skill_http_disconnect_retains_run_history_and_cancels(context):
    with runtime_client(context) as http:
        assert isinstance(http.app, FastAPI)
        app = http.app
        identifier = uuid4()
        save(http, identifier)
        reference = {"id": str(identifier), "revision": 1}
        body = run_body(context, reference)
        cookie = http.cookies.get("__Host-craft_session")
        assert cookie is not None

        async def execute():
            started = asyncio.Event()
            body_read = False

            async def receive():
                nonlocal body_read
                if not body_read:
                    body_read = True
                    return {
                        "type": "http.request",
                        "body": msgspec.json.encode(body),
                        "more_body": False,
                    }
                await started.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                if message["type"] == "http.response.start":
                    started.set()
                    await asyncio.Event().wait()

            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/api/agent/runs",
                "raw_path": b"/api/agent/runs",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"host", b"testserver"),
                    (b"origin", b"https://testserver"),
                    (b"content-type", b"application/json"),
                    (b"cookie", ("__Host-craft_session=" + cookie).encode()),
                ],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 443),
            }
            await asyncio.wait_for(app(scope, receive, send), 10)
            assert started.is_set()
            assert app.state.engine.pool.checkedout() == 0

        asyncio.run(execute())
        runs = metadata.tables["agent_runs"]
        with context["owner"].connect() as db:
            assert (
                db.execute(
                    select(runs.c.status).where(runs.c.id == UUID(body["runId"]))
                ).scalar_one()
                == "cancelled"
            )
        assert pins(context) == [reference]
        history = http.get("/api/conversations/" + body["threadId"]).json()["messages"]
        assert any(
            m.get("activityType") == "craft.skills"
            and m["content"] == {"runId": body["runId"], "skills": [reference]}
            for m in history
        )
