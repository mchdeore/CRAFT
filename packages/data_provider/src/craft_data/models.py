"""Typed records shared by the local provider prototype."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal


class StorageMode(StrEnum):
    LOCAL_ORIGINALS = "local_originals"
    EXTERNAL_ORIGINALS = "external_originals"


class ValueState(StrEnum):
    VALUE = "value"
    MISSING = "missing"
    WITHHELD = "withheld"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    WITHDRAWN = "withdrawn"


class RepresentationKind(StrEnum):
    BODY_CHUNK = "body_chunk"
    DOCUMENT_SUMMARY = "document_summary"
    SECTION_SUMMARY = "section_summary"
    FACET = "facet"
    TABLE_PARAMETER = "table_parameter"
    RELATIONSHIP_DESCRIPTION = "relationship_description"


@dataclass(frozen=True)
class BlockInput:
    text: str
    location: str
    heading: str | None = None

    def __post_init__(self) -> None:
        if not self.location.strip():
            raise ValueError("block location must not be empty")
        if not isinstance(self.text, str):
            raise TypeError("block text must be text")


@dataclass(frozen=True)
class DocumentInfo:
    document_id: str
    provider: str
    remote_id: str | None
    scope_id: str | None
    name: str
    source_location: str
    mission: str
    silo: str | None
    stage: str | None
    classification: str
    synthetic: bool
    storage_mode: StorageMode
    remote_status: str


@dataclass(frozen=True)
class VersionInfo:
    version_id: str
    document_id: str
    sequence: int
    content_hash: str
    byte_length: int
    provider_version: str | None
    extraction_id: str
    extraction_method: str
    extracted_at: datetime
    source_key: str
    blob_path: str | None


@dataclass(frozen=True)
class Citation:
    provider: str
    item_id: str
    version: str
    block_id: str | None
    location: str | None
    content_hash: str
    source_key: str


@dataclass(frozen=True)
class RepresentationInfo:
    representation_id: str
    version_id: str
    extraction_id: str
    block_id: str | None
    kind: RepresentationKind
    purpose: str
    text: str
    text_hash: str
    source_key: str


@dataclass(frozen=True)
class BlockRecord:
    block_id: str
    version_id: str
    extraction_id: str
    ordinal: int
    text: str
    heading: str | None
    location: str
    text_hash: str
    source_key: str


@dataclass(frozen=True)
class SearchQuery:
    text: str
    principal: str
    limit: int = 10
    kinds: tuple[RepresentationKind, ...] = (RepresentationKind.BODY_CHUNK,)
    known_at: datetime | None = None
    version_mode: Literal["latest", "all"] = "latest"


@dataclass(frozen=True)
class SearchHit:
    representation_id: str
    document_id: str
    name: str
    mission: str
    score: int
    excerpt: str
    citation: Citation


@dataclass(frozen=True)
class SearchPage:
    total_hits: int
    hits: tuple[SearchHit, ...]


@dataclass(frozen=True)
class CubeValueInput:
    mission: str
    metric: str
    subject: str
    state: ValueState
    amount: Decimal | None
    unit: str | None
    event_time: datetime
    known_at: datetime
    source_version_id: str
    source_block_id: str
    source_location: str
    evidence_hash: str


@dataclass(frozen=True)
class CubeQuery:
    principal: str
    mission: str
    metric: str
    at: datetime
    known_at: datetime
    subjects: tuple[str, ...] | None = None
    target_unit: str | None = None


@dataclass(frozen=True)
class CubeValueResult:
    subject: str
    mission: str
    metric: str
    state: ValueState
    amount: Decimal | None
    unit: str | None
    event_time: datetime | None
    known_at: datetime | None
    source: Citation | None
    visible_history_count: int
    fact_id: str | None = None


@dataclass(frozen=True)
class CubeQueryResult:
    accessible: bool
    rows: tuple[CubeValueResult, ...]


@dataclass(frozen=True)
class CandidateValueInput:
    mission: str
    metric: str
    subject: str
    amount: Decimal
    unit: str
    event_time: datetime
    source_version_id: str
    source_block_id: str
    source_location: str
    evidence_hash: str


@dataclass(frozen=True)
class CandidateBatch:
    batch_id: str
    candidate_ids: tuple[str, ...]
    payload_hash: str


@dataclass(frozen=True)
class ApprovalDecision:
    """A test boundary result; this type is not production authorization."""

    batch_id: str
    payload_hash: str
    accepted: bool
    issuer: str


@dataclass(frozen=True)
class PublicationResult:
    status: Literal["rejected", "published", "replayed"]
    fact_ids: tuple[str, ...]
    inserted_count: int


@dataclass(frozen=True)
class ExternalRecord:
    """A bounded in-memory record fetched from an external-originals provider."""

    provider: str
    remote_id: str
    scope_id: str
    name: str
    source_location: str
    mission: str
    silo: str | None
    stage: str | None
    classification: str
    synthetic: bool
    provider_version: str
    body: bytes
    blocks: tuple[BlockInput, ...]
    extraction_method: str


@dataclass(frozen=True)
class ReconciliationResult:
    scan_id: str
    status: Literal["complete", "partial"]
    seen_count: int
    changes: tuple[tuple[str, str], ...]
    error_type: str | None = None


@dataclass(frozen=True)
class RelationInfo:
    edge_id: str
    edge_type: Literal["structural", "evidence", "similarity_candidate"]
    source_version_id: str
    target_version_id: str
    relation: str
    source_block_id: str | None = None
    model: str | None = None
    recipe: str | None = None
    score: float | None = None
