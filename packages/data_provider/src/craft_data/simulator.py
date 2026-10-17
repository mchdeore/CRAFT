"""Fictional paginated tenant and reconciliation with fail-closed deletions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from .models import BlockInput, ExternalRecord, ReconciliationResult
from .store import DocumentStore
from .timeutil import utc_now


class SimulatedTenantError(RuntimeError):
    """Base error for synthetic tenant behavior."""


class SimulatedCredentialExpired(SimulatedTenantError):
    pass


class SimulatedAccessDenied(SimulatedTenantError):
    pass


class SimulatedContentUnavailable(SimulatedTenantError):
    pass


class UnstableSnapshotError(SimulatedTenantError):
    pass


@dataclass(frozen=True)
class SimulatedVersion:
    version_id: str
    chunks: tuple[bytes, ...]
    blocks: tuple[BlockInput, ...]
    extraction_method: str = "simulated-extraction-blocks-v1"


@dataclass(frozen=True)
class SimulatedItem:
    provider: str
    remote_id: str
    scope_id: str
    name: str
    source_location: str
    mission: str
    classification: str
    current_version: str
    versions: tuple[SimulatedVersion, ...]
    silo: str | None = None
    stage: str | None = None
    synthetic: bool = True

    def version(self, version_id: str) -> SimulatedVersion:
        for version in self.versions:
            if version.version_id == version_id:
                return version
        raise SimulatedContentUnavailable("requested synthetic version is unavailable")


@dataclass(frozen=True)
class RemoteListing:
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
    current_version: str


@dataclass(frozen=True)
class TenantPage:
    items: tuple[RemoteListing, ...]
    next_cursor: str | None


class TenantSource(Protocol):
    provider: str
    scope_id: str

    def list_page(self, cursor: str | None) -> TenantPage: ...

    def read_content(self, remote_id: str, provider_version: str) -> tuple[bytes, ...]: ...

    def get_blocks(self, remote_id: str, provider_version: str) -> tuple[BlockInput, ...]: ...


class SimulatedTenant:
    """A small in-memory provider with fictional IDs and byte streams."""

    def __init__(
        self,
        items: tuple[SimulatedItem, ...] | list[SimulatedItem],
        *,
        page_size: int = 2,
        repeated_ids: tuple[str, ...] = (),
        denied_ids: tuple[str, ...] = (),
        fail_page: int | None = None,
        expired_credentials: bool = False,
        chunk_size: int = 7,
    ) -> None:
        if page_size < 1 or chunk_size < 1:
            raise ValueError("page_size and chunk_size must be positive")
        if not items:
            self.provider = "opentext-sim"
            self.scope_id = "fictional-root"
        else:
            self.provider = items[0].provider
            self.scope_id = items[0].scope_id
        if any(item.provider != self.provider or item.scope_id != self.scope_id for item in items):
            raise ValueError("one simulated tenant must represent one provider scope")
        self.items = tuple(items)
        self.page_size = page_size
        self.repeated_ids = frozenset(repeated_ids)
        self.denied_ids = frozenset(denied_ids)
        self.fail_page = fail_page
        self.expired_credentials = expired_credentials
        self.chunk_size = chunk_size

    def list_page(self, cursor: str | None) -> TenantPage:
        offset = 0 if cursor is None else int(cursor)
        page_number = offset // self.page_size
        if self.expired_credentials:
            raise SimulatedCredentialExpired("synthetic credential expired")
        if self.fail_page is not None and page_number == self.fail_page:
            raise SimulatedTenantError("synthetic listing failed")
        entries = list(self.items)
        for item in self.items:
            if item.remote_id in self.repeated_ids:
                entries.append(item)
        page_items = entries[offset : offset + self.page_size]
        next_offset = offset + self.page_size
        next_cursor = str(next_offset) if next_offset < len(entries) else None
        return TenantPage(tuple(_listing(item) for item in page_items), next_cursor)

    def read_content(self, remote_id: str, provider_version: str) -> tuple[bytes, ...]:
        item = self._item(remote_id)
        if remote_id in self.denied_ids:
            raise SimulatedAccessDenied("synthetic item permission denied")
        version = item.version(provider_version)
        return tuple(
            chunk for original in version.chunks for chunk in _split(original, self.chunk_size)
        )

    def get_blocks(self, remote_id: str, provider_version: str) -> tuple[BlockInput, ...]:
        return self._item(remote_id).version(provider_version).blocks

    def _item(self, remote_id: str) -> SimulatedItem:
        for item in self.items:
            if item.remote_id == remote_id:
                return item
        raise SimulatedContentUnavailable("synthetic item is unavailable")


class TenantReconciler:
    def __init__(
        self,
        store: DocumentStore,
        *,
        max_item_bytes: int = 8 * 1024 * 1024,
        max_scan_bytes: int = 32 * 1024 * 1024,
        clock=utc_now,
    ) -> None:
        self.store = store
        self.max_item_bytes = max_item_bytes
        self.max_scan_bytes = max_scan_bytes
        self.clock = clock

    def scan(self, tenant: TenantSource) -> ReconciliationResult:
        scan_id = str(uuid.uuid4())
        started_at = self.clock()
        listings: dict[str, RemoteListing] = {}
        seen_entries = 0
        try:
            cursor: str | None = None
            while True:
                page = tenant.list_page(cursor)
                for item in page.items:
                    seen_entries += 1
                    previous = listings.get(item.remote_id)
                    if previous is not None and previous != item:
                        raise UnstableSnapshotError(
                            "duplicate remote item changed during one synthetic scan"
                        )
                    listings[item.remote_id] = item
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor

            records: list[ExternalRecord] = []
            total_bytes = 0
            for item in sorted(listings.values(), key=lambda value: value.remote_id):
                chunks = tenant.read_content(item.remote_id, item.current_version)
                body = _join_bounded(chunks, self.max_item_bytes)
                total_bytes += len(body)
                if total_bytes > self.max_scan_bytes:
                    raise SimulatedTenantError("synthetic scan exceeded its byte limit")
                records.append(
                    ExternalRecord(
                        provider=item.provider,
                        remote_id=item.remote_id,
                        scope_id=item.scope_id,
                        name=item.name,
                        source_location=item.source_location,
                        mission=item.mission,
                        silo=item.silo,
                        stage=item.stage,
                        classification=item.classification,
                        synthetic=item.synthetic,
                        provider_version=item.current_version,
                        body=body,
                        blocks=tenant.get_blocks(item.remote_id, item.current_version),
                        extraction_method="simulated-tenant-blocks-v1",
                    )
                )
            return self.store.apply_reconciliation(
                records,
                provider=tenant.provider,
                scope_id=tenant.scope_id,
                scan_id=scan_id,
                started_at=started_at,
                max_item_bytes=self.max_item_bytes,
            )
        except Exception as error:
            return self.store.record_partial_scan(
                scan_id=scan_id,
                provider=tenant.provider,
                scope_id=tenant.scope_id,
                started_at=started_at,
                seen_count=len(listings),
                error_type=type(error).__name__,
            )


def _listing(item: SimulatedItem) -> RemoteListing:
    return RemoteListing(
        provider=item.provider,
        remote_id=item.remote_id,
        scope_id=item.scope_id,
        name=item.name,
        source_location=item.source_location,
        mission=item.mission,
        silo=item.silo,
        stage=item.stage,
        classification=item.classification,
        synthetic=item.synthetic,
        current_version=item.current_version,
    )


def _split(body: bytes, chunk_size: int) -> tuple[bytes, ...]:
    return tuple(body[offset : offset + chunk_size] for offset in range(0, len(body), chunk_size))


def _join_bounded(chunks: tuple[bytes, ...], limit: int) -> bytes:
    body = bytearray()
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise TypeError("tenant content chunks must be bytes")
        if len(body) + len(chunk) > limit:
            raise SimulatedTenantError("synthetic content exceeded its byte limit")
        body.extend(chunk)
    return bytes(body)
