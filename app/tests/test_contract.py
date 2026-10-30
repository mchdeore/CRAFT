"""The handlers, app/contract and the wiki's route and tool tables describe the same API."""

import importlib
import inspect
import json
import re
from typing import Any, get_args, get_origin

import jsonschema
import msgspec
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.accounts.handlers import Me, SignIn
from app.agent.handlers import RunRecovery
from app.infrastructure.errors import ApiError
from app.infrastructure.operations.pipeline import register_http
from app.infrastructure.operations.registry import Operation, registry, stream_output
from app.infrastructure.paths import ROOT
from app.infrastructure.shapes import ApiErrorBody, Shape
from app.tests.support.contract import (
    CONTRACT,
    typescript_definitions,
    typescript_schema,
    validate_chat_events,
)

ROUTES_TS = (CONTRACT / "routes.ts").read_text()


@pytest.fixture(scope="session")
def operations() -> dict[str, Operation]:
    importlib.import_module("app")  # importing the app declares every operation
    return registry


@pytest.fixture(scope="session")
def http_routes(operations) -> dict[str, Operation]:
    """Each operation by its "METHOD /path/:param" key, with the path register_http gives it."""
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    register_http(app)
    routes = {}
    for route in app.routes:
        assert isinstance(route, APIRoute)
        path = re.sub(r"\{([^}]+)\}", r":\1", route.path)
        for method in route.methods or ():
            routes[f"{method} {path}"] = route.endpoint.__dict__["__operation__"]
    return routes


def shapes_in(shape: Any) -> list[type[Shape]]:
    if stream_output(shape) is not None:
        return shapes_in(stream_output(shape))
    if get_origin(shape) is list:
        return shapes_in(get_args(shape)[0])
    return [shape] if inspect.isclass(shape) and issubclass(shape, Shape) else []


def type_name(shape: Any) -> str:
    return type_name(get_args(shape)[0]) + "[]" if get_origin(shape) is list else shape.__name__


def test_session_contract() -> None:
    example = json.loads((CONTRACT / "examples/session.json").read_text())
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
    assert "POST /api/session" in ROUTES_TS and "DELETE /api/session" in ROUTES_TS


def test_python_shapes_match_typescript(operations) -> None:
    """Every operation's shapes, and the shapes inside them, against types.ts by name."""
    definitions = typescript_definitions()
    # Each shape with the path parameters that fill its fields; types.ts may leave those out.
    shapes: dict[type[Shape], set[str]] = {}
    for operation in operations.values():
        for shape in shapes_in(operation.input):
            filled = set(re.findall(r"\{(\w+)\}", operation.path or ""))
            shapes[shape] = shapes.get(shape, set()) | filled
        for shape in shapes_in(operation.output):
            shapes.setdefault(shape, set())
    path_fields = {shape.__name__: filled for shape, filled in shapes.items()}
    _, components = msgspec.json.schema_components(shapes)
    checked = [name for name in components if name in definitions]
    assert "Me" in checked and "SubmitValues" in checked
    for name in checked:
        declared, python = definitions[name], components[name]
        left_out = path_fields.get(name, set()) - set(declared["properties"])
        assert set(declared["properties"]) == set(python["properties"]) - left_out, name
        assert set(declared.get("required", [])) == set(python.get("required", [])) - left_out, name
    declared, python = definitions["CapabilityCatalog"], components["CapabilityCatalog"]
    assert declared["properties"]["skills"]["items"]["type"] == "string"
    assert python["properties"]["skills"]["items"]["type"] == "string"
    assert set(declared["properties"]["skillsStatus"]["enum"]) == set(
        python["properties"]["skillsStatus"]["enum"]
    )


def test_error_codes_match_typescript(operations) -> None:
    # Importing the app doesn't import these two, which declare errors of their own.
    importlib.import_module("app.accounts.oidc")
    importlib.import_module("app.agent.runtime")
    emitted, pending = set(), [ApiError]
    while pending:
        error = pending.pop()
        if ".tests." not in error.__module__:
            emitted.add(error.code)
        pending += error.__subclasses__()
    assert set(typescript_definitions()["ErrorCode"]["enum"]) == emitted


def test_route_keys_match_registry(http_routes) -> None:
    declared = set(re.findall(r'"((?:GET|POST|PUT|PATCH|DELETE) /[^" ]+)"', ROUTES_TS))
    assert set(http_routes) == declared


def test_chat_stream_example_matches_browser_contract() -> None:
    events = [
        json.loads(line[6:])
        for line in (CONTRACT / "examples/chat-answer.sse").read_text().splitlines()
        if line.startswith("data: ")
    ]
    assert events and validate_chat_events(events)
    assert events[0]["type"] == "RUN_STARTED" and events[-1]["type"] == "RUN_FINISHED"
    assert any(event.get("activityType") == "craft.answer" for event in events)


def contract_section(text: str, heading: str) -> str:
    matched = re.search(
        r"^" + re.escape(heading) + r"\n(.*?)(?=^#{2,3} |\Z)", text, flags=re.M | re.S
    )
    assert matched is not None, "Required contract section is missing"
    return matched[1]


def test_contract_routes_match_app(http_routes) -> None:
    document = (ROOT / ".public/wiki/api-contract.md").read_text()
    table = contract_section(document, "### 5.1 Built")
    rows = re.findall(r"^\| `((?:GET|POST|PUT|PATCH|DELETE) [^`]+)` \|", table, re.M)
    assert set(rows) == set(http_routes)
    assert len(rows) == len(set(rows)), "Duplicate built route"


def test_contract_tools_match_registry(operations) -> None:
    document = (ROOT / ".public/wiki/api-contract.md").read_text()
    section = contract_section(document, "## 6. Agent tools")
    rows = re.findall(r"^\| `([a-z0-9_]+)` \|", section, re.M)
    expected = {op.tool_name for op in operations.values() if op.tool}
    assert set(rows) == expected
    assert len(rows) == len(expected), "Duplicate tool name"


def json_responses() -> dict[str, str]:
    """Each JSON route in routes.ts with its response type, such as "Job" or "Job[]"."""
    return dict(
        re.findall(r'"([A-Z]+ /[^"]+)": [^\n]*?response: T\.([A-Za-z]+(?:\[\])?)', ROUTES_TS)
    )


def route_examples() -> dict[str, str]:
    """The example file each routes.ts line names in its trailing comment."""
    return dict(
        re.findall(r'^\s*"([A-Z]+ /[^"]+)":.*//.*\bexample: ([\w-]+\.json)$', ROUTES_TS, re.M)
    )


def test_examples_convert_to_route_outputs(http_routes) -> None:
    examples, responses = route_examples(), json_responses()
    assert set(examples) == set(responses), "Each JSON route names its example"
    for route, file_name in examples.items():
        output = http_routes[route].output
        assert type_name(output) == responses[route], route
        msgspec.convert(json.loads((CONTRACT / "examples" / file_name).read_text()), output)


def test_examples_match_typescript() -> None:
    responses = json_responses()
    for route, file_name in route_examples().items():
        shape = responses[route]
        schema = typescript_schema(shape.removesuffix("[]"))
        if shape.endswith("[]"):
            schema = {
                "type": "array",
                "items": {"$ref": schema["$ref"]},
                "definitions": schema["definitions"],
            }
        example = json.loads((CONTRACT / "examples" / file_name).read_text())
        assert jsonschema.Draft7Validator(schema).is_valid(example), file_name


def test_run_recovery_example_wire_shape() -> None:
    example = json.loads((CONTRACT / "examples/run-recovery.json").read_text())
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
