"""Thin MCP adapter: registry, shapes, shared pipeline and encoded results."""

import inspect
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import msgspec
from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, TextContent, Tool
from starlette.concurrency import run_in_threadpool

from app.infrastructure.access import Caller
from app.infrastructure.errors import ApiError, NotFound
from app.infrastructure.operations.registry import Operation


def input_schema(operation: Operation) -> dict[str, Any]:
    if operation.input is None:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    schema = msgspec.json.schema(operation.input)
    if "$ref" in schema:
        root = schema["$ref"].rsplit("/", 1)[-1]
        return {**schema["$defs"][root], "$defs": schema["$defs"]}
    return schema


def error_result(error: ApiError) -> CallToolResult:
    encoded = {"code": error.code, "message": type(error).message}
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=msgspec.json.encode(encoded).decode())],
        structuredContent=encoded,
    )


def build_server(registry: Mapping[str, Operation], caller: Caller) -> Server:
    server = Server("craft")
    trusted = replace(caller, origin="mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=o.tool_name,
                description=inspect.getdoc(o.function) or "",
                inputSchema=input_schema(o),
            )
            for o in registry.values()
            if o.tool
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        operation = next((o for o in registry.values() if o.tool and o.tool_name == name), None)
        if operation is None:
            return error_result(NotFound())
        try:
            if inspect.iscoroutinefunction(operation.function):
                result = await operation.function(trusted, arguments)
            else:
                result = await run_in_threadpool(operation.function, trusted, arguments)
            encoded = msgspec.to_builtins(result)
            structured = (
                encoded
                if isinstance(encoded, dict)
                else {"items": encoded}
                if isinstance(encoded, list)
                else {}
            )
            return CallToolResult(
                content=[TextContent(type="text", text=msgspec.json.encode(encoded).decode())],
                structuredContent=structured,
            )
        except ApiError as error:
            return error_result(error)

    return server
