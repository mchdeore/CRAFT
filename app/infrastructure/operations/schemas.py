"""Registry schemas shared by the native tool reference and MCP adapter."""

from typing import Any

import msgspec

from app.infrastructure.operations.registry import Operation


def _schema(shape: Any) -> dict[str, Any]:
    schema = msgspec.json.schema(shape)
    if "$ref" in schema:
        root = schema["$ref"].rsplit("/", 1)[-1]
        return {**schema["$defs"][root], "$defs": schema["$defs"]}
    return schema


def input_schema(operation: Operation) -> dict[str, Any]:
    if operation.input is None:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    return _schema(operation.input)


def output_schema(operation: Operation) -> dict[str, Any] | None:
    """Describe the logical JSON value; raw responses/streams have no JSON schema."""
    try:
        return _schema(operation.output)
    except TypeError:
        return None
