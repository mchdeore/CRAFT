"""Isolated SQLite and filesystem store for generated or supplied local documents."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .extraction import checked_blocks
from .markdown import ReferenceRenderer, atomic_write_text, render_reference
from .models import (
    BlockInput,
    BlockRecord,
    Citation,
    DocumentInfo,
    ExternalRecord,
    ReconciliationResult,
    StorageMode,
    VersionInfo,
)
from .schema import SCHEMA, migrate_representation_times
from .timeutil import dump_datetime, load_datetime, utc_now

Clock = Callable[[], datetime]


class VersionConflictError(ValueError):
    """A provider reused a version key for different bytes."""


class ExternalOriginalUnavailableError(RuntimeError):
    """The external-originals store intentionally has no source binary."""


class DocumentStore:
    """A package-local store with an explicit root and persistence mode."""

    def __init__(
        self,
        root: str | Path,
        *,
        mode: StorageMode,
        clock: Clock = utc_now,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.mode = StorageMode(mode)
        self.database_path = self.root / "document-store.sqlite3"
        self.clock = clock
        self.root.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            connection.executescript(SCHEMA)
            # executescript commits the initial transaction; keep the migration
            # check, column addition, and conservative backfill atomic together.
            connection.execute("BEGIN IMMEDIATE")
            migrate_representation_times(connection)

    @contextmanager
    def read_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_local_document(
        self,
        *,
        name: str,
        source_location: str,
        mission: str,
        classification: str,
        synthetic: bool,
        silo: str | None = None,
        stage: str | None = None,
    ) -> DocumentInfo:
        if self.mode is not StorageMode.LOCAL_ORIGINALS:
            raise ValueError("local documents require local_originals mode")
        _required(name, "name")
        _required(source_location, "source_location")
        _required(mission, "mission")
        _required(classification, "classification")
        document_id = str(uuid.uuid4())
        created_at = dump_datetime(self.clock())
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO documents (
                    document_id, provider, remote_id, scope_id, name, source_location,
                    mission, silo, stage, classification, synthetic, storage_mode,
                    remote_status, created_at, updated_at
                ) VALUES (?, 'local', NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    document_id,
                    name,
                    source_location,
                    mission,
                    silo,
                    stage,
                    classification,
                    int(synthetic),
                    self.mode.value,
                    created_at,
                    created_at,
                ),
            )
        return self.get_document(document_id)

    def rename_document(self, document_id: str, *, name: str, source_location: str) -> DocumentInfo:
        _required(name, "name")
        _required(source_location, "source_location")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT provider FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if row is None:
                raise KeyError(document_id)
            if row["provider"] != "local":
                raise ValueError("external names are changed by provider reconciliation")
            connection.execute(
                "UPDATE documents SET name = ?, source_location = ?, updated_at = ? "
                "WHERE document_id = ?",
                (name, source_location, dump_datetime(self.clock()), document_id),
            )
        return self.get_document(document_id)

    def ingest_local(
        self,
        document_id: str,
        body: bytes,
        blocks: Iterable[BlockInput],
        *,
        extraction_method: str,
        provider_version: str | None = None,
    ) -> VersionInfo:
        if self.mode is not StorageMode.LOCAL_ORIGINALS:
            raise ValueError("local ingestion requires local_originals mode")
        if not isinstance(body, bytes):
            raise TypeError("body must be bytes")
        _required(extraction_method, "extraction_method")
        checked = checked_blocks(blocks)
        version: VersionInfo | None = None
        extraction_created = False
        pending_blobs: list[Path] = []
        try:
            with self.transaction() as connection:
                document = connection.execute(
                    "SELECT * FROM documents WHERE document_id = ?", (document_id,)
                ).fetchone()
                if document is None:
                    raise KeyError(document_id)
                if document["storage_mode"] != StorageMode.LOCAL_ORIGINALS.value:
                    raise ValueError("document storage mode does not match this store")
                version, _, extraction_created = self._record_content(
                    connection,
                    document,
                    body,
                    checked,
                    extraction_method=extraction_method,
                    provider_version=provider_version,
                    pending_blob_paths=pending_blobs,
                )
        except BaseException:
            for blob_path in pending_blobs:
                blob_path.unlink(missing_ok=True)
            raise
        assert version is not None
        if extraction_created:
            self.refresh_reference(version.version_id, version.extraction_id)
        return version

    def ingest_external(
        self,
        record: ExternalRecord,
        *,
        max_bytes: int = 8 * 1024 * 1024,
    ) -> tuple[DocumentInfo, VersionInfo]:
        """Store derived data while retaining the fetched bytes in memory only."""
        if self.mode is not StorageMode.EXTERNAL_ORIGINALS:
            raise ValueError("external ingestion requires external_originals mode")
        _validate_external_record(record, max_bytes)
        scan_id = str(uuid.uuid4())
        started_at = self.clock()
        versions = self._apply_external_records(
            (record,),
            provider=record.provider,
            scope_id=record.scope_id,
            scan_id=scan_id,
            started_at=started_at,
            reconcile_deletions=False,
        )
        return self.get_external_document(record.provider, record.remote_id), versions[0]

    def _apply_external_records(
        self,
        records: Sequence[ExternalRecord],
        *,
        provider: str,
        scope_id: str,
        scan_id: str,
        started_at: datetime,
        reconcile_deletions: bool,
    ) -> tuple[VersionInfo, ...]:
        if self.mode is not StorageMode.EXTERNAL_ORIGINALS:
            raise ValueError("external reconciliation requires external_originals mode")
        if any(record.provider != provider or record.scope_id != scope_id for record in records):
            raise ValueError("every record must belong to the reconciled provider scope")
        seen_ids = [record.remote_id for record in records]
        if len(seen_ids) != len(set(seen_ids)):
            raise ValueError("a reconciliation batch may contain each remote item once")

        versions: list[VersionInfo] = []
        references: list[tuple[str, str]] = []
        changes: list[tuple[str, str, str | None]] = []
        finished_at = dump_datetime(self.clock())
        with self.transaction() as connection:
            for record in sorted(records, key=lambda item: item.remote_id):
                existing = connection.execute(
                    "SELECT * FROM documents WHERE provider = ? AND remote_id = ?",
                    (provider, record.remote_id),
                ).fetchone()
                before = _document_snapshot(existing) if existing is not None else None
                old_provider_version = None
                if existing is not None:
                    old_provider_version = connection.execute(
                        "SELECT 1 FROM provider_version_map WHERE document_id = ? "
                        "AND provider_version = ?",
                        (existing["document_id"], record.provider_version),
                    ).fetchone()
                document_id = self._upsert_external_document(connection, record)
                document = connection.execute(
                    "SELECT * FROM documents WHERE document_id = ?", (document_id,)
                ).fetchone()
                assert document is not None
                version, version_created, extraction_created = self._record_content(
                    connection,
                    document,
                    record.body,
                    checked_blocks(record.blocks),
                    extraction_method=record.extraction_method,
                    provider_version=record.provider_version,
                )
                versions.append(version)
                references.append((version.version_id, version.extraction_id))
                current = _document_snapshot(
                    connection.execute(
                        "SELECT * FROM documents WHERE document_id = ?", (document_id,)
                    ).fetchone()
                )
                if before is None:
                    changes.append((document_id, "added", version.version_id))
                else:
                    if before["remote_status"] == "deleted":
                        changes.append((document_id, "restored", version.version_id))
                    if before["name"] != current["name"]:
                        changes.append((document_id, "renamed", version.version_id))
                    if before["source_location"] != current["source_location"]:
                        changes.append((document_id, "moved", version.version_id))
                    if (
                        before["mission"] != current["mission"]
                        or before["classification"] != current["classification"]
                    ):
                        changes.append((document_id, "metadata", version.version_id))
                    if version_created:
                        changes.append((document_id, "new_version", version.version_id))
                    elif record.provider_version and old_provider_version is None:
                        changes.append((document_id, "version_alias", version.version_id))
                    if extraction_created:
                        changes.append((document_id, "metadata", version.version_id))

            seen = set(seen_ids)
            if reconcile_deletions:
                rows = connection.execute(
                    """
                    SELECT document_id, remote_id FROM documents
                    WHERE provider = ? AND scope_id = ? AND remote_status = 'active'
                    """,
                    (provider, scope_id),
                ).fetchall()
                for row in rows:
                    if row["remote_id"] not in seen:
                        connection.execute(
                            "UPDATE documents SET remote_status = 'deleted', updated_at = ? "
                            "WHERE document_id = ?",
                            (finished_at, row["document_id"]),
                        )
                        changes.append((row["document_id"], "deleted", None))

            connection.execute(
                """
                INSERT INTO scan_runs (
                    scan_id, provider, scope_id, status, seen_count, change_count,
                    error_type, started_at, finished_at
                ) VALUES (?, ?, ?, 'complete', ?, ?, NULL, ?, ?)
                """,
                (
                    scan_id,
                    provider,
                    scope_id,
                    len(records),
                    len(changes),
                    dump_datetime(started_at),
                    finished_at,
                ),
            )
            for document_id, change_type, source_version_id in changes:
                connection.execute(
                    """
                    INSERT INTO change_events (
                        event_id, scan_id, document_id, change_type,
                        source_version_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        scan_id,
                        document_id,
                        change_type,
                        source_version_id,
                        finished_at,
                    ),
                )

        for version_id, extraction_id in references:
            self.refresh_reference(version_id, extraction_id)
        return tuple(versions)

    def apply_reconciliation(
        self,
        records: Sequence[ExternalRecord],
        *,
        provider: str,
        scope_id: str,
        scan_id: str,
        started_at: datetime,
        max_item_bytes: int = 8 * 1024 * 1024,
    ) -> ReconciliationResult:
        if any(record.body and len(record.body) > max_item_bytes for record in records):
            raise ValueError("reconciliation record exceeds the item byte limit")
        version_rows = self._apply_external_records(
            records,
            provider=provider,
            scope_id=scope_id,
            scan_id=scan_id,
            started_at=started_at,
            reconcile_deletions=True,
        )
        with self.read_connection() as connection:
            events = connection.execute(
                "SELECT document_id, change_type FROM change_events "
                "WHERE scan_id = ? ORDER BY event_id",
                (scan_id,),
            ).fetchall()
        return ReconciliationResult(
            scan_id=scan_id,
            status="complete",
            seen_count=len(version_rows),
            changes=tuple((row["document_id"], row["change_type"]) for row in events),
        )

    def record_partial_scan(
        self,
        *,
        scan_id: str,
        provider: str,
        scope_id: str,
        started_at: datetime,
        seen_count: int,
        error_type: str,
    ) -> ReconciliationResult:
        finished_at = dump_datetime(self.clock())
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO scan_runs (
                    scan_id, provider, scope_id, status, seen_count, change_count,
                    error_type, started_at, finished_at
                ) VALUES (?, ?, ?, 'partial', ?, 0, ?, ?, ?)
                """,
                (
                    scan_id,
                    provider,
                    scope_id,
                    seen_count,
                    error_type,
                    dump_datetime(started_at),
                    finished_at,
                ),
            )
        return ReconciliationResult(
            scan_id=scan_id,
            status="partial",
            seen_count=seen_count,
            changes=(),
            error_type=error_type,
        )

    def _upsert_external_document(
        self, connection: sqlite3.Connection, record: ExternalRecord
    ) -> str:
        if self.mode is not StorageMode.EXTERNAL_ORIGINALS:
            raise ValueError("external document in local-originals store")
        existing = connection.execute(
            "SELECT document_id FROM documents WHERE provider = ? AND remote_id = ?",
            (record.provider, record.remote_id),
        ).fetchone()
        now = dump_datetime(self.clock())
        if existing is None:
            document_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO documents (
                    document_id, provider, remote_id, scope_id, name, source_location,
                    mission, silo, stage, classification, synthetic, storage_mode,
                    remote_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    document_id,
                    record.provider,
                    record.remote_id,
                    record.scope_id,
                    record.name,
                    record.source_location,
                    record.mission,
                    record.silo,
                    record.stage,
                    record.classification,
                    int(record.synthetic),
                    self.mode.value,
                    now,
                    now,
                ),
            )
        else:
            document_id = existing["document_id"]
            connection.execute(
                """
                UPDATE documents SET scope_id = ?, name = ?, source_location = ?,
                    mission = ?, silo = ?, stage = ?, classification = ?,
                    synthetic = ?, storage_mode = ?, remote_status = 'active', updated_at = ?
                WHERE document_id = ?
                """,
                (
                    record.scope_id,
                    record.name,
                    record.source_location,
                    record.mission,
                    record.silo,
                    record.stage,
                    record.classification,
                    int(record.synthetic),
                    self.mode.value,
                    now,
                    document_id,
                ),
            )
        return document_id

    def _record_content(
        self,
        connection: sqlite3.Connection,
        document: sqlite3.Row,
        body: bytes,
        blocks: Sequence[BlockInput],
        *,
        extraction_method: str,
        provider_version: str | None,
        pending_blob_paths: list[Path] | None = None,
    ) -> tuple[VersionInfo, bool, bool]:
        content_hash = hashlib.sha256(body).hexdigest()
        document_id = document["document_id"]
        mapped = None
        if provider_version is not None:
            mapped = connection.execute(
                """
                SELECT v.version_id, v.content_hash FROM provider_version_map p
                JOIN doc_versions v ON v.version_id = p.version_id
                WHERE p.document_id = ? AND p.provider_version = ?
                """,
                (document_id, provider_version),
            ).fetchone()
            if mapped is not None and mapped["content_hash"] != content_hash:
                raise VersionConflictError(
                    "provider version key resolved to different content bytes"
                )

        latest = connection.execute(
            "SELECT * FROM doc_versions WHERE document_id = ? ORDER BY sequence DESC LIMIT 1",
            (document_id,),
        ).fetchone()
        blob_path: str | None = None
        version_created = False

        if mapped is not None:
            version_row = connection.execute(
                "SELECT * FROM doc_versions WHERE version_id = ?", (mapped["version_id"],)
            ).fetchone()
        elif latest is not None and latest["content_hash"] == content_hash:
            version_row = latest
        else:
            sequence = 1 if latest is None else latest["sequence"] + 1
            version_id = str(uuid.uuid4())
            created_at = dump_datetime(self.clock())
            item_key = document["remote_id"] or document_id
            provider_version_key = provider_version or f"sha256:{content_hash}"
            source_key = (
                f"{document['provider']}:{item_key}/version:{provider_version_key}"
                f"/occurrence:{sequence}"
            )
            if self.mode is StorageMode.LOCAL_ORIGINALS:
                relative = Path("originals") / document_id / f"{sequence:06d}-{version_id}.bin"
                blob_path_on_disk = self.root / relative
                if pending_blob_paths is not None:
                    pending_blob_paths.append(blob_path_on_disk)
                blob_path_on_disk.parent.mkdir(parents=True, exist_ok=True)
                with blob_path_on_disk.open("xb") as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                blob_path = relative.as_posix()
            connection.execute(
                """
                INSERT INTO doc_versions (
                    version_id, document_id, sequence, content_hash, byte_length,
                    provider_version, source_key, storage_mode, blob_relative_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    document_id,
                    sequence,
                    content_hash,
                    len(body),
                    provider_version,
                    source_key,
                    self.mode.value,
                    blob_path,
                    created_at,
                ),
            )
            version_row = connection.execute(
                "SELECT * FROM doc_versions WHERE version_id = ?", (version_id,)
            ).fetchone()
            version_created = True

        assert version_row is not None
        if provider_version is not None:
            existing_map = connection.execute(
                """
                SELECT version_id FROM provider_version_map
                WHERE document_id = ? AND provider_version = ?
                """,
                (document_id, provider_version),
            ).fetchone()
            if existing_map is None:
                connection.execute(
                    "INSERT INTO provider_version_map(document_id, provider_version, version_id) "
                    "VALUES (?, ?, ?)",
                    (document_id, provider_version, version_row["version_id"]),
                )
            elif existing_map["version_id"] != version_row["version_id"]:
                raise VersionConflictError("provider version mapping changed")

        extraction_id, extraction_created = self._record_extraction(
            connection,
            document,
            version_row,
            blocks,
            extraction_method=extraction_method,
        )
        refreshed = connection.execute(
            "SELECT * FROM doc_versions WHERE version_id = ?", (version_row["version_id"],)
        ).fetchone()
        extraction_row = connection.execute(
            "SELECT created_at FROM extractions WHERE extraction_id = ?",
            (extraction_id,),
        ).fetchone()
        assert extraction_row is not None
        version_info = self._version_info(
            refreshed,
            extraction_id=extraction_id,
            extraction_method=extraction_method,
            extracted_at=extraction_row["created_at"],
        )
        return version_info, version_created, extraction_created

    def _record_extraction(
        self,
        connection: sqlite3.Connection,
        document: sqlite3.Row,
        version: sqlite3.Row,
        blocks: Sequence[BlockInput],
        *,
        extraction_method: str,
    ) -> tuple[str, bool]:
        serialized_blocks = json.dumps(
            [
                {"text": block.text, "location": block.location, "heading": block.heading}
                for block in blocks
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        output_hash = hashlib.sha256(serialized_blocks.encode("utf-8")).hexdigest()
        extraction = connection.execute(
            """
            SELECT extraction_id FROM extractions
            WHERE version_id = ? AND method = ? AND output_hash = ?
            """,
            (version["version_id"], extraction_method, output_hash),
        ).fetchone()
        connection.execute(
            "UPDATE extractions SET is_current = 0 WHERE version_id = ?",
            (version["version_id"],),
        )
        if extraction is not None:
            extraction_id = extraction["extraction_id"]
            connection.execute(
                "UPDATE extractions SET is_current = 1 WHERE extraction_id = ?",
                (extraction_id,),
            )
            return extraction_id, False

        extraction_id = str(uuid.uuid4())
        created_at = dump_datetime(self.clock())
        extraction_key = hashlib.sha256(
            f"{extraction_method}\x1f{output_hash}".encode()
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO extractions (
                extraction_id, version_id, method, output_hash, created_at, is_current
            ) VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                extraction_id,
                version["version_id"],
                extraction_method,
                output_hash,
                created_at,
            ),
        )
        for ordinal, block in enumerate(blocks):
            block_id = str(uuid.uuid4())
            text_hash = hashlib.sha256(block.text.encode("utf-8")).hexdigest()
            location_key = hashlib.sha256(block.location.encode("utf-8")).hexdigest()[:16]
            block_source = (
                f"{version['source_key']}/extraction:{extraction_key}"
                f"/block:{ordinal}:{location_key}"
            )
            connection.execute(
                """
                INSERT INTO blocks (
                    block_id, version_id, extraction_id, ordinal, location, heading,
                    text, text_hash, source_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    block_id,
                    version["version_id"],
                    extraction_id,
                    ordinal,
                    block.location,
                    block.heading,
                    block.text,
                    text_hash,
                    block_source,
                ),
            )
            representation_id = str(uuid.uuid4())
            representation_text = f"{block.heading}\n{block.text}" if block.heading else block.text
            representation_hash = hashlib.sha256(representation_text.encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO representations (
                    representation_id, version_id, extraction_id, block_id, kind,
                    purpose, text, text_hash, source_key, origin, generator_model, recipe,
                    created_at
                ) VALUES (?, ?, ?, ?, 'body_chunk', 'body_heading_v1',
                    ?, ?, ?, 'extracted', NULL, ?, ?)
                """,
                (
                    representation_id,
                    version["version_id"],
                    extraction_id,
                    block_id,
                    representation_text,
                    representation_hash,
                    f"{block_source}/representation:body_chunk:body_heading_v1",
                    extraction_method,
                    created_at,
                ),
            )
        return extraction_id, True

    def get_document(self, document_id: str) -> DocumentInfo:
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        if row is None:
            raise KeyError(document_id)
        return _document_info(row)

    def get_external_document(self, provider: str, remote_id: str) -> DocumentInfo:
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE provider = ? AND remote_id = ?",
                (provider, remote_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"{provider}:{remote_id}")
        return _document_info(row)

    def list_versions(self, document_id: str) -> tuple[VersionInfo, ...]:
        with self.read_connection() as connection:
            rows = connection.execute(
                """
                SELECT v.*, e.extraction_id, e.method, e.created_at AS extracted_at
                FROM doc_versions v JOIN extractions e ON e.version_id = v.version_id
                WHERE v.document_id = ? AND e.is_current = 1
                ORDER BY v.sequence
                """,
                (document_id,),
            ).fetchall()
        return tuple(
            self._version_info(
                row,
                extraction_id=row["extraction_id"],
                extraction_method=row["method"],
                extracted_at=row["extracted_at"],
            )
            for row in rows
        )

    def get_version(self, version_id: str) -> VersionInfo:
        with self.read_connection() as connection:
            row = connection.execute(
                """
                SELECT v.*, e.extraction_id, e.method, e.created_at AS extracted_at
                FROM doc_versions v JOIN extractions e ON e.version_id = v.version_id
                WHERE v.version_id = ? AND e.is_current = 1
                """,
                (version_id,),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return self._version_info(
            row,
            extraction_id=row["extraction_id"],
            extraction_method=row["method"],
            extracted_at=row["extracted_at"],
        )

    def get_blocks(
        self, version_id: str, extraction_id: str | None = None
    ) -> tuple[BlockRecord, ...]:
        with self.read_connection() as connection:
            if extraction_id is None:
                row = connection.execute(
                    "SELECT extraction_id FROM extractions WHERE version_id = ? AND is_current = 1",
                    (version_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(version_id)
                extraction_id = row["extraction_id"]
            rows = connection.execute(
                "SELECT * FROM blocks WHERE version_id = ? AND extraction_id = ? ORDER BY ordinal",
                (version_id, extraction_id),
            ).fetchall()
        return tuple(_block_record(row) for row in rows)

    def read_version_bytes(self, version_id: str) -> bytes:
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT storage_mode, blob_relative_path FROM doc_versions WHERE version_id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        if row["storage_mode"] != StorageMode.LOCAL_ORIGINALS.value:
            raise ExternalOriginalUnavailableError(
                "external-originals mode does not retain a local source binary"
            )
        relative = Path(row["blob_relative_path"])
        path = (self.root / relative).resolve()
        originals_root = (self.root / "originals").resolve()
        if originals_root not in path.parents:
            raise RuntimeError("stored source path escaped the private originals folder")
        return path.read_bytes()

    def add_representation(
        self,
        version_id: str,
        *,
        kind: str,
        purpose: str,
        text: str,
        block_id: str | None = None,
        extraction_id: str | None = None,
        origin: str = "generated",
        generator_model: str | None = None,
        recipe: str | None = None,
    ) -> str:
        allowed_kinds = {
            "body_chunk",
            "document_summary",
            "section_summary",
            "facet",
            "table_parameter",
            "relationship_description",
        }
        if kind not in allowed_kinds:
            raise ValueError(f"unsupported representation kind: {kind}")
        if origin not in {"extracted", "generated", "explicit", "suggested"}:
            raise ValueError(f"unsupported representation origin: {origin}")
        _required(purpose, "purpose")
        if not isinstance(text, str):
            raise TypeError("representation text must be text")
        with self.transaction() as connection:
            if block_id is not None:
                block = connection.execute(
                    "SELECT extraction_id FROM blocks WHERE version_id = ? AND block_id = ?",
                    (version_id, block_id),
                ).fetchone()
                if block is None:
                    raise ValueError("block does not belong to source version")
                if extraction_id is not None and extraction_id != block["extraction_id"]:
                    raise ValueError("block and extraction do not match")
                extraction_id = block["extraction_id"]
            if extraction_id is None:
                extraction = connection.execute(
                    "SELECT extraction_id FROM extractions WHERE version_id = ? AND is_current = 1",
                    (version_id,),
                ).fetchone()
                if extraction is None:
                    raise KeyError(version_id)
                extraction_id = extraction["extraction_id"]
            version = connection.execute(
                """
                SELECT v.source_key FROM doc_versions v
                JOIN extractions e ON e.version_id = v.version_id
                WHERE v.version_id = ? AND e.extraction_id = ?
                """,
                (version_id, extraction_id),
            ).fetchone()
            if version is None:
                raise ValueError("extraction does not belong to source version")
            representation_id = str(uuid.uuid4())
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            block_key = "document" if block_id is None else block_id
            equivalent = connection.execute(
                "SELECT representation_id FROM representations "
                "WHERE version_id=? AND extraction_id=? AND block_id IS ? "
                "AND kind=? AND purpose=? AND origin=? AND generator_model IS ? "
                "AND recipe IS ? AND text_hash=? ORDER BY rowid LIMIT 1",
                (
                    version_id,
                    extraction_id,
                    block_id,
                    kind,
                    purpose,
                    origin,
                    generator_model,
                    recipe,
                    text_hash,
                ),
            ).fetchone()
            if equivalent is not None:
                return str(equivalent["representation_id"])
            identity = hashlib.sha256(
                json.dumps(
                    [kind, purpose, origin, generator_model, recipe, text_hash],
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            source_key = (
                f"{version['source_key']}/extraction:{extraction_id}"
                f"/block:{block_key}/representation:{kind}:{identity}"
            )
            existing = connection.execute(
                "SELECT representation_id FROM representations WHERE source_key=?",
                (source_key,),
            ).fetchone()
            if existing is not None:
                return str(existing["representation_id"])
            connection.execute(
                """
                INSERT INTO representations (
                    representation_id, version_id, extraction_id, block_id, kind,
                    purpose, text, text_hash, source_key, origin, generator_model, recipe,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    representation_id,
                    version_id,
                    extraction_id,
                    block_id,
                    kind,
                    purpose,
                    text,
                    text_hash,
                    source_key,
                    origin,
                    generator_model,
                    recipe,
                    dump_datetime(self.clock()),
                ),
            )
            return representation_id

    def get_representation(self, representation_id: str) -> dict[str, Any]:
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM representations WHERE representation_id = ?",
                (representation_id,),
            ).fetchone()
        if row is None:
            raise KeyError(representation_id)
        return dict(row)

    def set_mission_permission(self, *, principal: str, mission: str, can_read: bool) -> None:
        _required(principal, "principal")
        _required(mission, "mission")
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO mission_grants(principal, mission, can_read) VALUES (?, ?, ?)
                ON CONFLICT(principal, mission) DO UPDATE SET can_read = excluded.can_read
                """,
                (principal, mission, int(can_read)),
            )

    def assign_tag(
        self,
        document_id: str,
        *,
        label_key: str,
        label_value: str,
        origin: str,
        version_id: str | None = None,
        evidence_block_id: str | None = None,
    ) -> str:
        reserved = {"mission", "classification", "silo", "stage", "owner"}
        if label_key.casefold() in reserved:
            raise ValueError("trusted document fields cannot be assigned as free-form tags")
        if origin not in {"explicit", "rule", "accepted_model"}:
            raise ValueError("tag assignments require explicit, rule, or accepted_model origin")
        tag_id = str(uuid.uuid4())
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tag_assignments (
                    assignment_id, document_id, version_id, label_key, label_value,
                    origin, evidence_block_id, assigned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tag_id,
                    document_id,
                    version_id,
                    label_key,
                    label_value,
                    origin,
                    evidence_block_id,
                    dump_datetime(self.clock()),
                ),
            )
        return tag_id

    def propose_tag(
        self,
        document_id: str,
        version_id: str,
        *,
        label_key: str,
        label_value: str,
        origin: str,
        recipe: str,
        evidence_block_id: str | None,
        model: str | None = None,
        confidence: str | None = None,
    ) -> str:
        if origin not in {"rule", "model"}:
            raise ValueError("tag proposals must come from a rule or model")
        _required(recipe, "recipe")
        if origin == "model" and not model:
            raise ValueError("model proposals retain the model identifier")
        proposal_id = str(uuid.uuid4())
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tag_proposals (
                    proposal_id, document_id, version_id, label_key, label_value,
                    origin, model, recipe, evidence_block_id, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    document_id,
                    version_id,
                    label_key,
                    label_value,
                    origin,
                    model,
                    recipe,
                    evidence_block_id,
                    confidence,
                    dump_datetime(self.clock()),
                ),
            )
        return proposal_id

    def tag_proposals(self, version_id: str) -> tuple[dict[str, Any], ...]:
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tag_proposals WHERE version_id = ? ORDER BY proposal_id",
                (version_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def refresh_reference(
        self,
        version_id: str,
        extraction_id: str | None = None,
        *,
        renderer: ReferenceRenderer = render_reference,
    ) -> Path:
        with self.read_connection() as connection:
            if extraction_id is None:
                extraction = connection.execute(
                    "SELECT extraction_id FROM extractions WHERE version_id = ? AND is_current = 1",
                    (version_id,),
                ).fetchone()
                if extraction is None:
                    raise KeyError(version_id)
                extraction_id = extraction["extraction_id"]
            rows = connection.execute(
                """
                SELECT d.*, v.sequence, v.content_hash, v.provider_version,
                    v.source_key AS version_source_key, e.method, e.output_hash,
                    e.created_at AS extracted_at, b.block_id, b.ordinal, b.text,
                    b.heading, b.location, b.text_hash, b.source_key AS block_source_key
                FROM documents d
                JOIN doc_versions v ON v.document_id = d.document_id
                JOIN extractions e ON e.version_id = v.version_id
                LEFT JOIN blocks b ON b.version_id = v.version_id
                    AND b.extraction_id = e.extraction_id
                WHERE v.version_id = ? AND e.extraction_id = ?
                ORDER BY b.ordinal
                """,
                (version_id, extraction_id),
            ).fetchall()
        if not rows:
            raise KeyError((version_id, extraction_id))
        first = rows[0]
        blocks = tuple(
            BlockInput(text=row["text"], location=row["location"], heading=row["heading"])
            for row in rows
            if row["block_id"] is not None
        )
        metadata: dict[str, object] = {
            "provider": first["provider"],
            "item": first["remote_id"] or first["document_id"],
            "remote_version": first["provider_version"],
            "content_hash": first["content_hash"],
            "extraction_hash": first["output_hash"],
            "extraction_method": first["method"],
            "extraction_date": first["extracted_at"],
            "classification": first["classification"],
            "source_location": first["source_location"],
            "synthetic": bool(first["synthetic"]),
        }
        content = renderer(name=first["name"], metadata=metadata, blocks=blocks)
        reference = (
            self.root
            / "references"
            / first["document_id"]
            / f"{first['sequence']:06d}-{extraction_id}.md"
        )
        atomic_write_text(reference, content)
        return reference

    def citation(
        self,
        version_id: str,
        *,
        block_id: str | None = None,
        location: str | None = None,
    ) -> Citation:
        with self.read_connection() as connection:
            row = connection.execute(
                """
                SELECT d.provider, d.remote_id, d.document_id, v.provider_version,
                    v.sequence, v.content_hash, v.source_key,
                    b.source_key AS block_source_key, b.location
                FROM doc_versions v JOIN documents d ON d.document_id = v.document_id
                LEFT JOIN blocks b ON b.version_id = v.version_id AND b.block_id = ?
                WHERE v.version_id = ?
                """,
                (block_id, version_id),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        item_id = row["remote_id"] or row["document_id"]
        version = row["provider_version"] or f"sha256:{row['content_hash']}#{row['sequence']}"
        source_key = row["block_source_key"] or row["source_key"]
        return Citation(
            provider=row["provider"],
            item_id=item_id,
            version=version,
            block_id=block_id,
            location=location or row["location"],
            content_hash=row["content_hash"],
            source_key=source_key,
        )

    def _version_info(
        self,
        row: sqlite3.Row,
        *,
        extraction_id: str,
        extraction_method: str,
        extracted_at: str | datetime | None = None,
    ) -> VersionInfo:
        extracted = (
            load_datetime(extracted_at)
            if isinstance(extracted_at, str)
            else extracted_at
            if isinstance(extracted_at, datetime)
            else load_datetime(row["created_at"])
        )
        return VersionInfo(
            version_id=row["version_id"],
            document_id=row["document_id"],
            sequence=row["sequence"],
            content_hash=row["content_hash"],
            byte_length=row["byte_length"],
            provider_version=row["provider_version"],
            extraction_id=extraction_id,
            extraction_method=extraction_method,
            extracted_at=extracted,
            source_key=row["source_key"],
            blob_path=row["blob_relative_path"],
        )


def _required(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _validate_external_record(record: ExternalRecord, max_bytes: int) -> None:
    if not isinstance(record.body, bytes):
        raise TypeError("external body must be bounded bytes")
    if len(record.body) > max_bytes:
        raise ValueError("external body exceeds the in-memory byte limit")
    for field_name in (
        "provider",
        "remote_id",
        "scope_id",
        "name",
        "source_location",
        "mission",
        "classification",
        "provider_version",
        "extraction_method",
    ):
        _required(getattr(record, field_name), field_name)


def _document_info(row: sqlite3.Row) -> DocumentInfo:
    return DocumentInfo(
        document_id=row["document_id"],
        provider=row["provider"],
        remote_id=row["remote_id"],
        scope_id=row["scope_id"],
        name=row["name"],
        source_location=row["source_location"],
        mission=row["mission"],
        silo=row["silo"],
        stage=row["stage"],
        classification=row["classification"],
        synthetic=bool(row["synthetic"]),
        storage_mode=StorageMode(row["storage_mode"]),
        remote_status=row["remote_status"],
    )


def _block_record(row: sqlite3.Row) -> BlockRecord:
    return BlockRecord(
        block_id=row["block_id"],
        version_id=row["version_id"],
        extraction_id=row["extraction_id"],
        ordinal=row["ordinal"],
        text=row["text"],
        heading=row["heading"],
        location=row["location"],
        text_hash=row["text_hash"],
        source_key=row["source_key"],
    )


def _document_snapshot(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        raise ValueError("document row is missing")
    return {key: row[key] for key in row.keys()}
