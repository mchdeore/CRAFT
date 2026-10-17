"""Timezone-safe serialization used by the SQLite prototype."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)


def dump_datetime(value: datetime) -> str:
    return normalize_datetime(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def load_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
