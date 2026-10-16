"""Request-owned source summaries with canonical citations and uncommitted filing evidence."""

from typing import Annotated, Literal

import msgspec

from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import RawFile, Shape
from app.library.handlers import CitationHandle
from app.summarize import service

Hash = Annotated[str, msgspec.Meta(pattern=r"^[0-9a-f]{64}$")]


class SelectedSource(Shape):
    item: str
    version: Annotated[int, msgspec.Meta(ge=1)]
    sha256: Hash


class SummarySource(Shape):
    kind: Literal["document", "upload"]
    file_name: str
    version: Hash
    item: str | None
    provider_version: int | None
    simulated: bool


class SummaryCitation(Shape):
    handle: CitationHandle | None
    block: int
    block_version: Hash
    locator: str
    quote: str
    heading_path: str


class SummarySentence(Shape):
    text: str
    citations: list[SummaryCitation]


class FilingSuggestion(Shape):
    value: str | None
    confidence: float
    evidence: list[SummaryCitation]
    path: str | None


class Confidence(Shape):
    score: Annotated[float, msgspec.Meta(ge=0, le=1)]
    level: Literal["high", "medium", "low"]
    reason: str


class SummaryResult(Shape):
    source: SummarySource
    sentences: list[SummarySentence]
    mission: FilingSuggestion
    silo: FilingSuggestion
    stage: FilingSuggestion
    confidence: Confidence
    status: Literal["supported", "insufficient_evidence"]


@operation(SIGNED_IN, post="/summarize")
async def selected(caller: Caller, request: SelectedSource) -> SummaryResult:
    """Summarize an exact readable source version without filing or keeping an original."""
    return await service.selected(caller, request)


@operation(SIGNED_IN, post="/summarize/upload")
async def upload(caller: Caller, request: RawFile) -> SummaryResult:
    """Summarize an owned simulated PDF, Word or workbook in request memory."""
    return await service.upload(caller, request)
