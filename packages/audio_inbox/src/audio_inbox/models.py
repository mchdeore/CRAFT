"""Immutable audio, transcript and review records."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Segment:
    segment_id: str
    text: str
    start: float | None = None
    end: float | None = None
    speaker: str | None = None


@dataclass(frozen=True)
class TranscriptReply:
    model: str
    text: str
    segments: list[Segment]


@dataclass(frozen=True)
class Citation:
    segment_id: str
    quote: str
    start: float | None = None
    end: float | None = None


@dataclass(frozen=True)
class GoalCandidate:
    title: str
    action: str
    citations: list[Citation]
    due_date: str | None = None
    uncertainty: str | None = None


@dataclass(frozen=True)
class AssetRef:
    id: str
    path: Path
    sha256: str
    media_type: str
    duration: float
    size_bytes: int


@dataclass(frozen=True)
class TranscriptVersion:
    id: str
    job_id: str
    parent_version_id: str | None
    model: str
    text: str
    segments: list[Segment]


@dataclass(frozen=True)
class Proposal:
    id: str
    version_id: str
    title: str
    action: str
    citations: list[Citation]
    status: str
    due_date: str | None = None
    uncertainty: str | None = None


@dataclass(frozen=True)
class JobState:
    id: str
    source_name: str
    status: str
    asset_id: str | None
    version_id: str | None
    error: str | None
