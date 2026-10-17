"""Client bindings derived from declared server operations."""

from collections.abc import Iterable
from typing import Protocol

from craft_runtime.native_mcp import NativeMCPSource
from craft_runtime.runner import Binding
from craft_runtime.state import RuntimeErrorCode


class DeclaredOperation(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def tool(self) -> bool: ...

    @property
    def effects(self) -> str: ...

    @property
    def tool_name(self) -> str: ...


def source_from_registry(
    url: str, token: str, operations: Iterable[DeclaredOperation]
) -> NativeMCPSource:
    bindings: dict[str, Binding] = {}
    for operation in operations:
        if not operation.tool or operation.effects != "NONE" or operation.tool_name == "ask_agent":
            continue
        if operation.tool_name in bindings:
            raise RuntimeErrorCode("ambiguous_tool_name")
        bindings[operation.tool_name] = Binding(operation.name)
    return NativeMCPSource(url, token, bindings)
