"""The TypeScript contract in app/contract, compiled once per test session. Tests never run npm."""

import functools
import json
import subprocess

import jsonschema
import pytest

from app.infrastructure.paths import ROOT

CONTRACT = ROOT / "app/contract"


def require_node_modules() -> None:
    if not (CONTRACT / "node_modules").is_dir():
        pytest.skip("run npm ci --prefix app/contract")


@functools.cache  # a skip raises, so only a finished run is cached
def typescript_definitions() -> dict:
    """Every declaration in types.ts as JSON Schema, from one generator run per session."""
    require_node_modules()
    output = subprocess.check_output(
        [
            str(CONTRACT / "node_modules/.bin/ts-json-schema-generator"),
            "--path",
            str(CONTRACT / "types.ts"),
            "--type",
            "*",
        ],
        text=True,
    )
    return json.loads(output)["definitions"]


def typescript_schema(name: str) -> dict:
    return {"$ref": f"#/definitions/{name}", "definitions": typescript_definitions()}


def validate_chat_events(events) -> bool:
    require_node_modules()
    checked = subprocess.run(
        ["node", str(CONTRACT / "validate-stream.mjs")],
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
