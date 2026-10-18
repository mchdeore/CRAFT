"""Shared current permission, version, and derivation visibility predicates."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .timeutil import dump_datetime


@dataclass(frozen=True)
class VisibilityScope:
    principal: str
    version_mode: str = "latest"
    known_at: datetime | None = None
    known_time: str | None = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.principal, str) or not self.principal.strip():
            raise ValueError("principal must be non-empty text")
        if not isinstance(self.version_mode, str) or self.version_mode not in {"latest", "all"}:
            raise ValueError("version_mode must be latest or all")
        if self.known_at is not None and not isinstance(self.known_at, datetime):
            raise ValueError("known_at must be a timezone-aware datetime")
        object.__setattr__(
            self, "known_time", None if self.known_at is None else dump_datetime(self.known_at)
        )

    def versions(
        self, version: str = "v", document: str = "d", grant: str = "g"
    ) -> tuple[str, tuple[object, ...]]:
        clauses = [
            f"{document}.remote_status = 'active'",
            f"{grant}.principal = ?",
            f"{grant}.can_read = 1",
        ]
        parameters: list[object] = [self.principal]
        if self.known_time is not None:
            clauses.append(f"{version}.created_at <= ?")
            parameters.append(self.known_time)
        if self.version_mode == "latest":
            newer_time = " AND newer_version.created_at <= ?" if self.known_time else ""
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM doc_versions newer_version "
                f"WHERE newer_version.document_id={version}.document_id "
                f"AND newer_version.sequence>{version}.sequence{newer_time})"
            )
            if self.known_time is not None:
                parameters.append(self.known_time)
        return " AND ".join(clauses), tuple(parameters)

    def extractions(self, extraction: str) -> tuple[str, tuple[object, ...]]:
        if self.known_time is None:
            return f"{extraction}.is_current = 1", ()
        return (
            f"{extraction}.created_at <= ? AND NOT EXISTS ("
            "SELECT 1 FROM extractions newer_extraction "
            f"WHERE newer_extraction.version_id={extraction}.version_id "
            "AND newer_extraction.created_at <= ? "
            f"AND (newer_extraction.created_at>{extraction}.created_at OR "
            f"(newer_extraction.created_at={extraction}.created_at "
            f"AND newer_extraction.rowid>{extraction}.rowid)))",
            (self.known_time, self.known_time),
        )

    def created(self, alias: str) -> tuple[str, tuple[object, ...]]:
        """Unknown legacy creation times remain available only to current reads."""
        if self.known_time is None:
            return "1", ()
        return f"{alias}.created_at <= ?", (self.known_time,)
