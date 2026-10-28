"""Checked chart and calculation tools use the shared operation boundary."""

from typing import Annotated, Any, ClassVar, Literal

import msgspec

from app.conversations.handlers import WorkspaceFile
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape
from app.library.handlers import CitationHandle
from app.tools import service


class ChartInput(Shape):
    max_bytes: ClassVar[int] = 1024 * 1024
    spec: dict[str, Any]
    format: Literal["svg", "png"] = "svg"
    title: Annotated[str, msgspec.Meta(min_length=1, max_length=200)] | None = None
    sources: Annotated[list[CitationHandle], msgspec.Meta(max_length=50)] = []


class ChartFiles(Shape):
    svg: WorkspaceFile
    png: WorkspaceFile
    spec: WorkspaceFile
    csv: WorkspaceFile


class ChartResult(Shape):
    title: str
    format: Literal["svg", "png"]
    media_type: str
    content_base64: str | None
    file: WorkspaceFile | None
    files: ChartFiles | None
    citations: list[CitationHandle]
    simulated: bool


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
    skills_status: Literal["not_installed", "installed"]


def workspace_chart(spec: dict[str, Any], title: str | None) -> dict[str, str]:
    """A checked chart's SVG, Vega-Lite spec and CSV text, keyed by file suffix."""
    return service.workspace_chart(spec, title)


@operation(SIGNED_IN, get="/tools/capabilities", tool=True)
def list_capabilities(caller: Caller) -> CapabilityCatalog:
    """List available read tools and up to 100 owned saved-skill names."""
    return service.capabilities(caller)


@operation(SIGNED_IN, post="/tools/chart", tool=True)
async def make_chart(caller: Caller, request: ChartInput) -> ChartResult:
    """Render inline chart data with its cited sources; keep SVG, PNG, spec and CSV files."""
    return ChartResult(**await service.chart(caller, request))


@operation(SIGNED_IN, post="/tools/calculation-check", tool=True)
async def check_calculation(caller: Caller, request: CalculationInput) -> CalculationResult:
    """Check a numeric or unit expression independently, without its source context."""
    return CalculationResult(**await service.calculation(caller, request))
