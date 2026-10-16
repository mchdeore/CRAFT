"""Implemented account response forms match the adopted wire contract."""

import json
import subprocess
from pathlib import Path

import msgspec

from app.accounts.handlers import Me, SignIn
from app.infrastructure.paths import ROOT
from app.infrastructure.shapes import ApiErrorBody


def test_session_contract() -> None:
    example = json.loads(Path("api/examples/session.json").read_text())
    body = msgspec.convert(example, Me)
    assert set(msgspec.to_builtins(body)) == {"id", "username", "displayName", "roles"}
    assert set(msgspec.structs.fields(SignIn)[n].encode_name for n in [0, 1]) == {
        "username",
        "password",
    }
    error = ApiErrorBody(code="not_signed_in", message="Not signed in.", request_id="synthetic")
    assert set(msgspec.to_builtins(error)) == {"code", "message", "requestId"}
    declarations = Path("api/routes.ts").read_text()
    assert "POST /api/session" in declarations and "DELETE /api/session" in declarations


def typescript_schema(name: str) -> dict:
    executable = ROOT / "api/node_modules/.bin/ts-json-schema-generator"
    if not executable.exists():
        subprocess.run(
            [
                "npm",
                "ci",
                "--prefix",
                str(ROOT / "api"),
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    output = subprocess.check_output(
        [str(executable), "--path", str(ROOT / "api/types.ts"), "--type", name], text=True
    )
    return json.loads(output)


def root_schema(schema: dict) -> dict:
    if "$ref" not in schema:
        return schema
    collection, key = schema["$ref"].split("/")[-2:]
    return schema[collection][key]


def test_python_shapes_match_compiled_typescript() -> None:
    import jsonschema

    for shape in [Me, SignIn]:
        compiled = typescript_schema(shape.__name__)
        declared = root_schema(compiled)
        python = root_schema(msgspec.json.schema(shape))
        assert set(declared["properties"]) == set(python["properties"])
        assert set(declared["required"]) == set(python["required"])
        if shape is Me:
            jsonschema.validate(
                json.loads((ROOT / "api/examples/session.json").read_text()), compiled
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
            r'"((?:GET|POST|PUT|PATCH|DELETE) /[^" ]+)"', (ROOT / "api/routes.ts").read_text()
        )
    )
    assert actual == declared


def validate_chat_events(events) -> bool:
    import jsonschema

    checked = subprocess.run(
        ["node", str(ROOT / "api/validate-stream.mjs")],
        input=json.dumps(events),
        text=True,
        capture_output=True,
    )
    if checked.returncode != 0:
        return False
    schema = typescript_schema("AnswerMeta")
    validator = jsonschema.Draft7Validator(schema)
    return all(
        validator.is_valid(event["content"])
        for event in events
        if event.get("activityType") == "craft.answer"
    )


def test_chat_stream_example_matches_browser_contract() -> None:
    events = [
        json.loads(line[6:])
        for line in (ROOT / "api/examples/chat-answer.sse").read_text().splitlines()
        if line.startswith("data: ")
    ]
    assert events and validate_chat_events(events)
    assert events[0]["type"] == "RUN_STARTED" and events[-1]["type"] == "RUN_FINISHED"
    assert any(event.get("activityType") == "craft.answer" for event in events)
