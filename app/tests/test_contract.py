"""Implemented account response forms match the adopted wire contract."""

import json
import subprocess
from pathlib import Path

import msgspec
import pytest

from app.accounts.handlers import (
    IssuedWorkspaceToken,
    Me,
    NewWorkspaceToken,
    SignIn,
    WorkspaceToken,
)
from app.agent.handlers import RunRecovery, RunRecoveryRef
from app.cube.handlers import MissionRef, ValueRef
from app.health.handlers import QueueCounts, QueueHealth
from app.infrastructure.paths import ROOT
from app.infrastructure.shapes import ApiErrorBody
from app.jobs.handlers import Job, JobPage, JobPageRequest, JobProgress
from app.library.handlers import (
    CatalogDocument,
    DocumentCatalog,
    DocumentList,
    Graph,
    GraphEdge,
    GraphGroup,
    GraphNode,
    RelationsRequest,
)
from app.notifications.handlers import Notification, NotificationPageRequest
from app.skills.handlers import (
    SaveSkill,
    Skill,
    SkillCatalog,
    SkillList,
    SkillRef,
    SkillSummary,
    SkillVersionRef,
)
from app.tools.handlers import Capability, CapabilityCatalog
from app.workspace.handlers import Workspace


def test_session_contract() -> None:
    example = json.loads(Path("app/contract/examples/session.json").read_text())
    body = msgspec.convert(example, Me)
    assert set(msgspec.to_builtins(body)) == {
        "id",
        "username",
        "displayName",
        "roles",
        "language",
        "email",
    }
    assert set(msgspec.structs.fields(SignIn)[n].encode_name for n in [0, 1]) == {
        "username",
        "password",
    }
    error = ApiErrorBody(code="not_signed_in", message="Not signed in.", request_id="synthetic")
    assert set(msgspec.to_builtins(error)) == {"code", "message", "requestId"}
    declarations = Path("app/contract/routes.ts").read_text()
    assert "POST /api/session" in declarations and "DELETE /api/session" in declarations


def typescript_schema(name: str) -> dict:
    executable = ROOT / "app/contract/node_modules/.bin/ts-json-schema-generator"
    if not executable.exists():
        subprocess.run(
            [
                "npm",
                "ci",
                "--prefix",
                str(ROOT / "app/contract"),
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    output = subprocess.check_output(
        [str(executable), "--path", str(ROOT / "app/contract/types.ts"), "--type", name], text=True
    )
    return json.loads(output)


def root_schema(schema: dict) -> dict:
    if "$ref" not in schema:
        return schema
    collection, key = schema["$ref"].split("/")[-2:]
    return schema[collection][key]


@pytest.mark.slow
def test_python_shapes_match_compiled_typescript() -> None:
    import jsonschema

    for shape in [
        Me,
        SignIn,
        NewWorkspaceToken,
        WorkspaceToken,
        IssuedWorkspaceToken,
        MissionRef,
        ValueRef,
        Capability,
        CapabilityCatalog,
        DocumentList,
        CatalogDocument,
        DocumentCatalog,
        RelationsRequest,
        GraphNode,
        GraphEdge,
        GraphGroup,
        Graph,
        Job,
        JobPage,
        JobPageRequest,
        JobProgress,
        Notification,
        NotificationPageRequest,
        QueueCounts,
        QueueHealth,
        RunRecoveryRef,
        RunRecovery,
        SaveSkill,
        Skill,
        SkillCatalog,
        SkillList,
        SkillRef,
        SkillSummary,
        SkillVersionRef,
        Workspace,
    ]:
        compiled = typescript_schema(shape.__name__)
        declared = root_schema(compiled)
        python = root_schema(msgspec.json.schema(shape))
        assert set(declared["properties"]) == set(python["properties"])
        assert set(declared.get("required", [])) == set(python.get("required", []))
        if shape is CapabilityCatalog:
            assert declared["properties"]["skills"]["items"]["type"] == "string"
            assert python["properties"]["skills"]["items"]["type"] == "string"
            assert set(declared["properties"]["skillsStatus"]["enum"]) == set(
                python["properties"]["skillsStatus"]["enum"]
            )
        if shape is Me:
            jsonschema.validate(
                json.loads((ROOT / "app/contract/examples/session.json").read_text()), compiled
            )


def test_route_keys_match_running_registry(settings) -> None:
    import re

    from app import create_app

    app = create_app(settings)
    actual = {
        method + " " + re.sub(r"\{([^}]+)\}", r":\1", getattr(route, "path", ""))
        for route in app.routes
        for method in (getattr(route, "methods", None) or [])
    }
    declared = set(
        re.findall(
            r'"((?:GET|POST|PUT|PATCH|DELETE) /[^" ]+)"',
            (ROOT / "app/contract/routes.ts").read_text(),
        )
    )
    assert actual == declared


def validate_chat_events(events) -> bool:
    import jsonschema

    checked = subprocess.run(
        ["node", str(ROOT / "app/contract/validate-stream.mjs")],
        input=json.dumps(events),
        text=True,
        capture_output=True,
    )
    if checked.returncode != 0:
        return False
    shapes = {"craft.answer": "AnswerMeta", "craft.originals": "AnswerOriginals"}
    validators = {
        kind: jsonschema.Draft7Validator(typescript_schema(shape)) for kind, shape in shapes.items()
    }
    return all(
        validators[event["activityType"]].is_valid(event["content"])
        for event in events
        if event.get("activityType") in validators
    )


def test_chat_stream_example_matches_browser_contract() -> None:
    events = [
        json.loads(line[6:])
        for line in (ROOT / "app/contract/examples/chat-answer.sse").read_text().splitlines()
        if line.startswith("data: ")
    ]
    assert events and validate_chat_events(events)
    assert events[0]["type"] == "RUN_STARTED" and events[-1]["type"] == "RUN_FINISHED"
    assert any(event.get("activityType") == "craft.answer" for event in events)


def contract_section(text: str, heading: str) -> str:
    import re

    matched = re.search(
        r"^" + re.escape(heading) + r"\n(.*?)(?=^#{2,3} |\Z)", text, flags=re.M | re.S
    )
    assert matched is not None, "Required contract section is missing"
    return matched[1]


def test_contract_routes_match_app(settings) -> None:
    import re

    from app import create_app

    document = (ROOT / ".public/wiki/api-contract.md").read_text()
    table = contract_section(document, "### 5.1 Built")
    rows = re.findall(r"^\| `((?:GET|POST|PUT|PATCH|DELETE)) ([^`]+)` \|", table, re.M)
    declared = {
        (method, re.sub(r":([A-Za-z][A-Za-z0-9_]*)", r"{\1}", path)) for method, path in rows
    }
    app = create_app(settings)
    try:
        actual = {
            (method, getattr(route, "path", ""))
            for route in app.routes
            for method in (getattr(route, "methods", None) or [])
            if method not in {"HEAD", "OPTIONS"}
        }
        assert declared == actual
        assert len(rows) == len(declared), "Duplicate built route"
    finally:
        app.state.engine.dispose()


def test_contract_tools_match_registry(settings) -> None:
    import re

    from app import create_app
    from app.infrastructure.operations.registry import registry

    app = create_app(settings)
    try:
        document = (ROOT / ".public/wiki/api-contract.md").read_text()
        section = contract_section(document, "## 6. Agent tools")
        rows = re.findall(r"^\| `([a-z0-9_]+)` \|", section, re.M)
        expected = {op.tool_name for op in registry.values() if op.tool}
        assert set(rows) == expected
        assert len(rows) == len(expected), "Duplicate tool name"
    finally:
        app.state.engine.dispose()


@pytest.mark.slow
def test_contract_json_examples() -> None:
    import re

    import jsonschema

    document = (ROOT / ".public/wiki/api-contract.md").read_text()
    section = contract_section(document, "## 15. Examples")
    rows = re.findall(
        r"^\| `([A-Z]+ /[^`]+)` \| `([A-Za-z]+(?:\[\])?)` \| `([^`]+)` \|", section, re.M
    )
    expected = {
        route: shape
        for route, shape in re.findall(
            r'"([A-Z]+ /[^"]+)": [^\n]*?response: T\.([A-Za-z]+(?:\[\])?)',
            (ROOT / "app/contract/routes.ts").read_text(),
        )
    }
    assert {route: shape for route, shape, _ in rows} == expected
    assert len(rows) == len(expected), "Duplicate response example"
    schemas = {}
    for _, shape, path in rows:
        name = shape.removesuffix("[]")
        if name not in schemas:
            schemas[name] = typescript_schema(name)
        schema = schemas[name]
        if shape.endswith("[]"):
            schema = {
                "type": "array",
                "items": {"$ref": schema["$ref"]},
                "definitions": schema["definitions"],
            }
        example = ROOT / path
        assert example.parent == ROOT / "app/contract/examples" and example.is_file()
        assert jsonschema.Draft7Validator(schema).is_valid(json.loads(example.read_text()))


def test_run_recovery_example_wire_shape() -> None:
    import jsonschema

    example = json.loads((ROOT / "app/contract/examples/run-recovery.json").read_text())
    parsed = msgspec.convert(example, RunRecovery)
    assert msgspec.to_builtins(parsed) == example
    assert parsed.status == "paused" and parsed.approval_status == "pending"
    assert parsed.run_id is not None and parsed.approval_id is not None
    assert parsed.can_resume is False
    validator = jsonschema.Draft7Validator(typescript_schema("RunRecovery"))
    assert validator.is_valid(example)
    for field in ("runId", "startedAt", "endedAt", "approvalId", "approvalStatus"):
        assert validator.is_valid(example | {field: None})
    for field in example:
        assert not validator.is_valid(
            {key: value for key, value in example.items() if key != field}
        )
    assert not validator.is_valid(example | {"status": "unknown"})
    assert not validator.is_valid(example | {"approvalStatus": "unknown"})
    assert not validator.is_valid(example | {"canResume": "true"})
