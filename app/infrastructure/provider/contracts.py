"""Shared source boundary for indexing, citations, cube checks and downloads.

Every adapter reads (``Provider``). Only the simulated source is also written to, by
the seed and ``craft provider put|rm`` (``WritableProvider``); a real source never is.
"""

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

type Item = dict[str, Any]
type Change = dict[str, Any]


class Provider(Protocol):
    def scan(self) -> list[Change]:
        """Reconcile with the source; a partial or permission-denied scan deletes nothing."""
        ...

    def items(self) -> dict[str, Item]: ...
    def list_changes(self, cursor: int) -> tuple[list[Change], int]: ...
    def get_item(self, item: str) -> Item: ...
    def read_content(self, item: str, version: int, *, max_bytes: int | None = None) -> bytes:
        """Read a version, rejecting sources larger than max_bytes when supplied."""
        ...


@runtime_checkable
class WritableProvider(Provider, Protocol):
    root: Path

    def path(self, relative: str) -> Path: ...
    def write_content(self, path: str, content: bytes) -> None: ...
    def delete_item(self, item: str) -> None: ...
