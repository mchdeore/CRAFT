"""Dictionary, dated mission values, comparison and source evidence."""

from typing import Annotated, Literal
from uuid import UUID

import msgspec

from app.cube import service
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape
from app.library.handlers import CitationHandle


class Mission(Shape):
    key: str
    title: str
    current_stage: str
    aliases: list[str]
    simulated: bool


class ListMissions(Shape):
    query: str = ""
    limit: Annotated[int, msgspec.Meta(ge=1, le=100)] = 20


@operation(SIGNED_IN, get="/cube/missions", tool=True)
def list_missions(caller: Caller, request: ListMissions) -> list[Mission]:
    """Resolve readable mission names and aliases. Use before cube reads; for example sim-01."""
    return service.list_missions(caller, request)


def change_stage(settings: Settings, mission: str, stage: str) -> None:
    """Maintain one mission's lifecycle stage through the local owner command."""
    service.change_stage(settings, mission, stage)


class Unit(Shape):
    unit: str
    multiplier: str
    offset: str


class Parameter(Shape):
    key: str
    label: str
    unit: str
    min: float | None
    max: float | None
    whole: bool
    units: list[Unit]


class Condition(Shape):
    parameter: str
    op: Literal["eq", "ge", "le", "between"]
    value: str
    unit: str
    high: str | None = None


class Scope(Shape):
    silo: str | None = None
    stage: str | None = None
    at: str | None = None
    known_at: str | None = None


class FindMissions(Scope, kw_only=True):
    conditions: Annotated[list[Condition], msgspec.Meta(min_length=1, max_length=12)]
    limit: Annotated[int, msgspec.Meta(ge=1, le=20)] = 20


class MissionRef(Scope, kw_only=True):
    key: str


class CompareMissions(Scope, kw_only=True):
    keys: Annotated[list[str], msgspec.Meta(min_length=2, max_length=5)]
    parameters: Annotated[list[str], msgspec.Meta(min_length=1, max_length=100)]


class ValueRef(Shape):
    id: UUID


class CubeValue(Shape):
    id: UUID
    mission: str
    silo: str
    stage: str | None
    element: UUID | None
    parameter: str
    kind: Literal["exact", "range", "unknown", "not_applicable"]
    low: float | None
    high: float | None
    unit: str
    source_number: str | None
    source_unit: str | None
    valid_from: str
    recorded_at: str
    citation: CitationHandle
    withdrawn: bool
    approval_id: UUID | None
    simulated: bool


class MatchedMission(Shape):
    mission: Mission
    matched: list[CubeValue]


class FoundMissions(Shape):
    missions: list[MatchedMission]
    at: str
    known_at: str
    truncated: bool


class MissionState(Shape):
    mission: Mission
    values: list[CubeValue]
    gaps: list[str]
    at: str
    known_at: str


class ComparisonRow(Shape):
    mission: Mission
    values: list[CubeValue | None]


class Comparison(Shape):
    parameters: list[str]
    rows: list[ComparisonRow]
    at: str
    known_at: str


class ValueEvidence(Shape):
    value: CubeValue
    quote: str
    conversion: str
    citation_status: Literal["current", "changed", "newer_version", "withdrawn"]


@operation(SIGNED_IN, get="/cube/parameters", tool=True)
def list_parameters(caller: Caller) -> list[Parameter]:
    """Read the parameter dictionary and registered units. Use before filtering, such as kg or g."""
    return service.list_parameters(caller)


@operation(SIGNED_IN, post="/cube/find", tool=True)
def find_missions(caller: Caller, request: FindMissions) -> FoundMissions:
    """Find readable missions matching all conditions. For example dry mass at most 150 kg."""
    return service.find_missions(caller, request)


@operation(SIGNED_IN, get="/cube/missions/{key}", tool=True)
def get_mission(caller: Caller, request: MissionRef) -> MissionState:
    """Read accepted mission values on a date. Use a key or alias, for example sim-01."""
    return service.get_mission(caller, request)


@operation(SIGNED_IN, post="/cube/compare", tool=True)
def compare_missions(caller: Caller, request: CompareMissions) -> Comparison:
    """Compare two to five readable missions in one scope. For example their mass and power."""
    return service.compare_missions(caller, request)


@operation(SIGNED_IN, get="/cube/values/{id}", tool=True)
def get_value_evidence(caller: Caller, request: ValueRef) -> ValueEvidence:
    """Read the cited source row and conversion for a value. Use an accepted value identifier."""
    return service.get_value_evidence(caller, request)
