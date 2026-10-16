"""Synthetic operations in an isolated registry for transport verification."""

from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.operations import DATA, operation
from app.infrastructure.operations.registry import registry
from app.infrastructure.shapes import Shape


class ProbeInput(Shape):
    value: int


class ProbeOutput(Shape):
    value: int
    result_value: int


def fixture_registry():
    snapshot = registry.copy()
    executed = []
    try:

        @operation(PUBLIC, get="/mcp-probe-read", tool=True)
        def probe_read(caller: Caller, request: ProbeInput) -> ProbeOutput:
            """Double one test number. Use to test a read; for example value 7."""
            return ProbeOutput(value=request.value, result_value=request.value * 2)

        @operation(PUBLIC, post="/mcp-probe-write", tool=True, effects=DATA)
        def probe_write(caller: Caller, request: ProbeInput) -> None:
            """Attempt a test write. Use to test refusal; for example value 7."""
            executed.append(True)

        @operation(PUBLIC, get="/mcp-probe-hidden")
        def probe_hidden(caller: Caller) -> None:
            return None

        local = {name: op for name, op in registry.items() if name not in snapshot}
    finally:
        registry.clear()
        registry.update(snapshot)
    return local, executed
