"""Checked chart and calculation tools use the shared operation boundary."""

from typing import Annotated, Any, ClassVar, Literal

import msgspec

from app.conversations.handlers import WorkspaceFile
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape
from app.tools import service


class ChartInput(Shape):
    max_bytes: ClassVar[int] = 1024 * 1024
    spec: dict[str, Any]
    format: Literal["svg", "png"]


class ChartResult(Shape):
    format: Literal["svg", "png"]
    media_type: str
    content_base64: str | None
    file: WorkspaceFile | None


class CalculationInput(Shape):
    max_bytes: ClassVar[int] = 4096
    expression: Annotated[str, msgspec.Meta(min_length=1, max_length=1000)]
    craft_result: str | float | None = None
    unit: str | None = None


class CalculationResult(Shape):
    expression: str
    wolfram_result: str
    craft_result: str | float | None
    agrees: bool
    checked_at: str


class Capability(Shape):
    name: Annotated[str, msgspec.Meta(pattern=r"^[a-z0-9_]{1,64}$")]
    description: Annotated[str, msgspec.Meta(min_length=1, max_length=240)]


class CapabilityCatalog(Shape):
    tools: list[Capability]
    skills: list[str]
    skills_status: Literal["not_installed"]


@operation(SIGNED_IN, get="/tools/capabilities", tool=True)
def list_capabilities(caller: Caller) -> CapabilityCatalog:
    """List available agent tools and whether saved skills are installed."""
    return service.capabilities()


@operation(SIGNED_IN, post="/tools/chart", tool=True)
async def make_chart(caller: Caller, request: ChartInput) -> ChartResult:
    """Render inline chart data locally; keep a generated file in the active workspace."""
    return ChartResult(**await service.chart(caller, request))


@operation(SIGNED_IN, post="/tools/calculation-check", tool=True)
async def check_calculation(caller: Caller, request: CalculationInput) -> CalculationResult:
    """Check a numeric or unit expression independently, without its source context."""
    return CalculationResult(**await service.calculation(caller, request))
