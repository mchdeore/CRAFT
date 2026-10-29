"""Dictionary, dated mission values, comparison and source evidence."""

from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID

import msgspec

from app.cube import service
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.operations import DATA, operation

if TYPE_CHECKING:
    from app.cube.publication import PreparedPublication
from app.infrastructure.shapes import Shape
from app.library.handlers import CitationHandle, Facet


class Mission(Shape):
    key: str
    title: str
    current_stage: str
    aliases: list[str]
    simulated: bool


class SimilarMissions(Shape):
    key: str
    facets: Annotated[list[Facet], msgspec.Meta(min_length=1, max_length=5)] | None = None
    limit: Annotated[int, msgspec.Meta(ge=1, le=10)] = 5


class SimilarMission(Shape):
    mission: Mission
    score: float | None
    comparable: bool
    facet_scores: dict[Facet, float | None]


class SimilarPage(Shape):
    missions: list[SimilarMission]
    facets: list[Facet]


class ExplainSimilarity(Shape):
    key: str
    other: str
    facets: Annotated[list[Facet], msgspec.Meta(min_length=1, max_length=5)] | None = None


class FacetExplanation(Shape):
    facet: Facet
    score: float | None
    text: str | None
    other_text: str | None
    sources: list[str]
    other_sources: list[str]


class SimilarityExplanation(Shape):
    facets: list[FacetExplanation]


@operation(SIGNED_IN, post="/cube/similar", tool=True)
def similar_missions(caller: Caller, request: SimilarMissions) -> SimilarPage:
    """Rank readable missions by cited facets; for example design and operations."""
    from app.cube.similarity import similar

    return similar(caller, request)


@operation(SIGNED_IN, post="/cube/similar/explain", tool=True)
def explain_similarity(caller: Caller, request: ExplainSimilarity) -> SimilarityExplanation:
    """Read both missions' permitted facet evidence; for example a design comparison."""
    from app.cube.similarity import explain

    return explain(caller, request)


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


class ValueLabels(Shape):
    ids: list[UUID]


class ValueLabel(Shape):
    id: UUID
    label: str


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


class PrepareValues(Shape):
    handle: CitationHandle
    mission: str | None = None
    silo: str | None = None
    stage: str | None = None
    valid_from: str | None = None


class Candidate(Shape):
    row: int
    header: str
    parameter: str | None
    element: str | None
    source_number: str
    source_unit: str | None
    kind: Literal["exact", "range", "unknown", "not_applicable"]
    low: float | None
    high: float | None
    citation: CitationHandle
    problem: str | None


class Candidates(Shape):
    mission: str | None
    silo: str | None
    stage: str | None
    valid_from: str | None
    candidates: list[Candidate]
    problems: list[str]


@operation(SIGNED_IN, post="/cube/candidates", tool=True)
def prepare_values(caller: Caller, request: PrepareValues) -> Candidates:
    """Read cited candidates without writing values; for example a mass-budget table."""
    return service.prepare_values(caller, request)


class SubmitValues(Shape):
    mission: str
    silo: str
    valid_from: str
    candidates: Annotated[list[Candidate], msgspec.Meta(min_length=1, max_length=100)]
    stage: str | None = None


class ValueReceipt(Shape):
    approval_id: UUID
    value_ids: list[UUID]


def _prepare_publication(caller: Caller, request: SubmitValues) -> "PreparedPublication":
    from app.cube.publication import prepare_submission

    return prepare_submission(caller, request)


@operation(
    SIGNED_IN,
    post="/cube/values",
    tool=True,
    effects=DATA,
    prepare_approved=_prepare_publication,
)
def submit_values(caller: Caller, request: SubmitValues) -> ValueReceipt:
    """Publish a reviewed batch of source candidates in one approval transaction."""
    return service.submit_values(caller, request)


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


@operation(SIGNED_IN)
def label_values(caller: Caller, request: ValueLabels) -> list[ValueLabel]:
    """Name the values among these IDs the caller's grants reach; the rest are left out.

    Other features (library relations) use it so the cube's value rule is applied in one place.
    """
    return service.label_values(caller, request)
