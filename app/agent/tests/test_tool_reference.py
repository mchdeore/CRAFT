"""The website reference describes real schemas and execution channels."""

from collections.abc import AsyncIterator
from dataclasses import replace
from uuid import uuid4

import jsonschema
import msgspec
import pytest
from fastapi.testclient import TestClient
from starlette.responses import Response

from app import create_app
from app.agent.handlers import list_tools
from app.infrastructure.access import Caller
from app.infrastructure.errors import NotSignedIn
from app.infrastructure.mcp.server import input_schema
from app.infrastructure.operations.registry import registry, tool_operations
from app.tests.support.chat import chat as chat
from app.tests.support.cube import cube_source as cube_source


def reference():
    return {item.name: item for item in list_tools(Caller(user_id=uuid4()))}


def test_reference_describes_actual_mcp_inputs():
    catalog = reference()
    assert set(catalog) == {operation.tool_name for operation in tool_operations(registry)}
    for operation in tool_operations(registry):
        assert catalog[operation.tool_name].input_schema == input_schema(operation)
    note = catalog["write_note"].input_schema
    assert note["type"] == "object"
    assert {"title", "body"} <= set(note["required"])
    assert note["properties"]["title"]["maxLength"] == 200
    assert "conversationId" in note["properties"]


def test_reference_covers_no_input_and_native_list_and_null_outputs():
    catalog = reference()
    assert catalog["list_parameters"].input_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert catalog["delete_note"].output_schema == {"type": "null"}
    notes = catalog["list_notes"].output_schema
    assert notes["type"] == "array"
    item_name = notes["items"]["$ref"].rsplit("/", 1)[-1]
    assert {"id", "title", "updatedAt"} <= set(notes["$defs"][item_name]["properties"])
    note = catalog["read_note"].output_schema
    assert note["type"] == "object"
    assert "body" in note["properties"]


def test_reference_distinguishes_api_chat_and_read_only_query():
    catalog = reference()
    assert catalog["ask_agent"].available_in == ["api", "mcp"]
    for name in ("write_note", "delete_note", "submit_values"):
        assert catalog[name].available_in == ["api", "mcp", "chat"]
        assert catalog[name].needs_approval is True
    assert catalog["search"].available_in == ["api", "mcp", "chat", "read_only_query"]
    assert catalog["search"].needs_approval is False
    assert {name for name, item in catalog.items() if "read_only_query" in item.available_in} == {
        operation.tool_name for operation in tool_operations(registry, agent=True)
    }


def test_reference_follows_registry_additions_without_a_static_catalog(monkeypatch):
    original = next(
        operation for operation in tool_operations(registry) if operation.effects == "NONE"
    )
    probe = replace(original, name="probe.reference_read", input=None, output=type(None))
    write = replace(probe, name="probe.reference_write", effects="DATA")
    hidden = replace(probe, name="probe.reference_hidden", tool=False)
    for operation in (probe, write, hidden):
        monkeypatch.setitem(registry, operation.name, operation)
    catalog = reference()
    assert catalog[probe.tool_name].available_in == ["api", "mcp", "chat", "read_only_query"]
    assert catalog[write.tool_name].available_in == ["api", "mcp", "chat"]
    assert catalog[write.tool_name].needs_approval is True
    assert catalog[probe.tool_name].output_schema == {"type": "null"}
    assert hidden.tool_name not in catalog


@pytest.mark.parametrize("output", [Response, list[Response], AsyncIterator[Response]])
def test_reference_does_not_invent_json_schema_for_raw_response(monkeypatch, output):
    original = next(
        operation for operation in tool_operations(registry) if operation.effects == "NONE"
    )
    raw = replace(original, name="probe.reference_raw", output=output)
    monkeypatch.setitem(registry, raw.name, raw)
    assert reference()[raw.tool_name].output_schema is None


def test_reference_schemas_validate_native_generated_payloads():
    catalog = reference()
    for item in catalog.values():
        jsonschema.Draft202012Validator.check_schema(item.input_schema)
        assert item.output_schema is not None
        jsonschema.Draft202012Validator.check_schema(item.output_schema)
    summary = {
        "id": str(uuid4()),
        "title": "Generated reference",
        "conversationId": None,
        "sizeBytes": 4,
        "createdAt": "2026-10-18T05:00:00Z",
        "updatedAt": "2026-10-18T05:00:00Z",
    }
    jsonschema.validate([summary], catalog["list_notes"].output_schema)
    jsonschema.validate({**summary, "body": "Text"}, catalog["read_note"].output_schema)
    jsonschema.validate(None, catalog["delete_note"].output_schema)
    jsonschema.validate({"title": "Generated", "body": "Text"}, catalog["write_note"].input_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(summary, catalog["read_note"].output_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"title": "x" * 201, "body": "Text"}, catalog["write_note"].input_schema
        )


def test_reference_requires_signed_in_caller():
    with pytest.raises(NotSignedIn):
        list_tools(Caller())


def test_reference_http_auth_and_wire_schemas(chat):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert http.get("/api/tools").status_code == 401
        assert chat["sign_in"](http).status_code == 200
        response = http.get("/api/tools")
        assert response.status_code == 200
        expected = msgspec.to_builtins(list_tools(chat["caller"]))
        assert response.json() == expected
        search = next(item for item in response.json() if item["name"] == "search")
        assert search["availableIn"] == ["api", "mcp", "chat", "read_only_query"]
        assert "query" in search["inputSchema"]["properties"]
        assert search["outputSchema"]["type"] == "object"
