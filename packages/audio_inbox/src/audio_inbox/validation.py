"""Shared deterministic validation; no model, file or database access."""

import math
import re
from datetime import date

from .models import Citation, GoalCandidate, Segment, TranscriptReply, TranscriptVersion


def _time(value: float | None, duration: float | None) -> bool:
    return value is None or (
        type(value) in (int, float)
        and (type(value) is int or math.isfinite(value))
        and value >= 0
        and (duration is None or value <= duration)
    )


def validate_transcript(
    reply: TranscriptReply, duration: float, max_chars: int = 200_000
) -> TranscriptReply:
    if (
        not isinstance(reply, TranscriptReply)
        or not isinstance(reply.text, str)
        or len(reply.text) > max_chars
    ):
        raise ValueError("invalid_transcript")
    if not isinstance(reply.model, str) or not reply.model.strip() or len(reply.segments) > 10000:
        raise ValueError("invalid_transcript")
    seen: set[str] = set()
    previous = -math.inf
    for segment in reply.segments:
        if (
            not isinstance(segment, Segment)
            or not isinstance(segment.segment_id, str)
            or not segment.segment_id
            or segment.segment_id in seen
        ):
            raise ValueError("invalid_segment_identity")
        seen.add(segment.segment_id)
        if not isinstance(segment.text, str) or len(segment.text) > max_chars:
            raise ValueError("invalid_segment_text")
        if not _time(segment.start, duration) or not _time(segment.end, duration):
            raise ValueError("invalid_segment_time")
        if segment.start is not None:
            if segment.start < previous:
                raise ValueError("segments_out_of_order")
            previous = segment.start
        if segment.start is not None and segment.end is not None and segment.start > segment.end:
            raise ValueError("invalid_segment_time")
        if segment.speaker is not None and (
            not isinstance(segment.speaker, str) or len(segment.speaker) > 200
        ):
            raise ValueError("invalid_speaker")
    return reply


def valid_goal(
    candidate: GoalCandidate,
    transcript: TranscriptVersion,
    duration: float | None = None,
    max_chars: int = 200_000,
) -> bool:
    if not isinstance(candidate, GoalCandidate):
        return False
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > max_chars
        for value in (candidate.title, candidate.action)
    ):
        return False
    if candidate.due_date is not None:
        if not isinstance(candidate.due_date, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", candidate.due_date
        ):
            return False
        try:
            date.fromisoformat(candidate.due_date)
        except ValueError:
            return False
    if candidate.uncertainty is not None and (
        not isinstance(candidate.uncertainty, str) or len(candidate.uncertainty) > max_chars
    ):
        return False
    if not isinstance(candidate.citations, list) or not 1 <= len(candidate.citations) <= 100:
        return False
    segments = {s.segment_id: s for s in transcript.segments}
    for citation in candidate.citations:
        if not isinstance(citation, Citation) or not isinstance(citation.segment_id, str):
            return False
        segment = segments.get(citation.segment_id)
        if (
            segment is None
            or not isinstance(citation.quote, str)
            or not citation.quote.strip()
            or len(citation.quote) > max_chars
            or citation.quote not in segment.text
        ):
            return False
        if not _time(citation.start, duration) or not _time(citation.end, duration):
            return False
        if citation.start is not None and (segment.start is None or citation.start < segment.start):
            return False
        if citation.end is not None and (segment.end is None or citation.end > segment.end):
            return False
        if (
            citation.start is not None
            and citation.end is not None
            and citation.start > citation.end
        ):
            return False
    return True
