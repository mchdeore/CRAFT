"""Small, immutable catalog for caller-fetched public research snapshots.

This module performs no network access and makes no claims about market meaning or
profitability. Timestamp values are recorded verbatim; callers own their semantics.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 100_000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_BAD_QS = {
    "key",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "id_token",
    "auth",
    "authorization",
    "secret",
    "client_secret",
    "sig",
    "signature",
}


class CatalogError(ValueError):
    """Invalid input or catalog operation."""


class ReviewRequired(CatalogError):
    """A dataset's observed structure differs from its previously reviewed shape."""


class CorruptSnapshot(CatalogError):
    """Stored snapshot bytes do not match their recorded digest."""


def _token(value: str, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value) or ".." in value:
        raise CatalogError(f"invalid {field}")
    return value


def _time(value: str) -> str:
    if value == "unknown":
        return value
    if not isinstance(value, str):
        raise CatalogError("time must be an ISO timestamp or explicit 'unknown'")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CatalogError("time must be an ISO timestamp or explicit 'unknown'") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CatalogError("time must include timezone offset or be 'unknown'")
    return value


def _canonical_source_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError("source_url is required")
    parts = urlsplit(value.strip())
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise CatalogError("source_url must be canonical https URL")
    if parts.username or parts.password:
        raise CatalogError("source_url must not include credentials")
    query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        if key.casefold() in _BAD_QS or key.casefold().startswith("x-amz-"):
            raise CatalogError("source_url query cannot include token/signature params")
        query.append((key, item))
    host = parts.hostname.lower()
    port = f":{parts.port}" if parts.port and parts.port != 443 else ""
    path = parts.path or "/"
    authority = f"[{host}]" if ":" in host else host
    return urlunsplit(("https", authority + port, path, urlencode(query), ""))


def _shape(data: bytes, fmt: str) -> tuple[dict[str, Any], int]:
    if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
        raise CatalogError("LFS pointer is not acquired dataset content")
    try:
        text = data.decode("utf-8-sig")
        if fmt == "json":
            value = json.loads(text)
            if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
                raise CatalogError("JSON must be an array of objects")
            if len(value) > MAX_ROWS:
                raise CatalogError("row limit exceeded")
            keys = sorted({key for row in value for key in row})
            types = {
                key: sorted({type(row[key]).__name__ for row in value if key in row})
                for key in keys
            }
            return {"format": fmt, "keys": keys, "types": types}, len(value)
        if fmt == "csv":
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            names = reader.fieldnames or []
            if not names or any(not isinstance(n, str) or not n.strip() for n in names):
                raise CatalogError("CSV needs nonblank header fields")
            if len(set(names)) != len(names):
                raise CatalogError("CSV needs unique header fields")
            count = 0
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise CatalogError("CSV row does not match its header")
                count += 1
                if count > MAX_ROWS:
                    raise CatalogError("row limit exceeded")
            return {"format": fmt, "fields": names}, count
    except (UnicodeDecodeError, json.JSONDecodeError, csv.Error) as exc:
        raise CatalogError("invalid UTF-8 CSV/JSON payload") from exc
    raise CatalogError("format must be 'csv' or 'json'")


class Catalog:
    """Immutable byte snapshots and append-only acquisition provenance."""

    def __init__(self, storage_root: str | Path):
        self.root = Path(storage_root)
        if self.root.is_symlink():
            raise CatalogError("storage root must be a real directory")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.blobs = self.root / "snapshots"
        if self.blobs.is_symlink():
            raise CatalogError("snapshot directory must be a real directory")
        self.blobs.mkdir(exist_ok=True, mode=0o700)
        self.db = self.root / "catalog.sqlite3"
        if self.db.is_symlink():
            raise CatalogError("catalog must be a real file")
        with self._connect() as con:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute(
                "CREATE TABLE IF NOT EXISTS datasets (namespace TEXT, dataset_id TEXT, "
                "shape TEXT NOT NULL, PRIMARY KEY(namespace,dataset_id))"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS acquisitions (id INTEGER PRIMARY KEY, "
                "namespace TEXT NOT NULL, dataset_id TEXT NOT NULL, kind TEXT NOT NULL, "
                "sha256 TEXT NOT NULL, source_url TEXT NOT NULL, provenance TEXT NOT NULL, "
                "license_status TEXT NOT NULL, fetched TEXT NOT NULL, published TEXT NOT NULL, "
                "observed TEXT NOT NULL, recipe TEXT, "
                "row_count INTEGER NOT NULL, byte_count INTEGER NOT NULL, format TEXT NOT NULL, "
                "FOREIGN KEY(namespace,dataset_id) REFERENCES datasets(namespace,dataset_id))"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS acquisition_sources ("
                "acquisition_id INTEGER NOT NULL REFERENCES acquisitions(id), "
                "source_id INTEGER NOT NULL REFERENCES acquisitions(id), "
                "PRIMARY KEY(acquisition_id,source_id))"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS acquisition_dataset "
                "ON acquisitions(namespace,dataset_id,id)"
            )
            con.execute("CREATE INDEX IF NOT EXISTS acquisition_hash ON acquisitions(sha256)")
        os.chmod(self.db, 0o600)

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.db, timeout=5.0, isolation_level=None)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            yield con
        finally:
            if con.in_transaction:
                con.rollback()
            con.close()

    @staticmethod
    def _read_blob(path: Path) -> bytes:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as error:
            raise CorruptSnapshot("snapshot is not a regular readable file") from error
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
                raise CorruptSnapshot("snapshot is not a bounded regular file")
            return stream.read(MAX_BYTES + 1)

    def _verify_existing_blob(self, path: Path, data: bytes, digest: str) -> None:
        if not path.exists() and not path.is_symlink():
            return
        existing = self._read_blob(path)
        if hashlib.sha256(existing).hexdigest() != digest or existing != data:
            raise CorruptSnapshot("content-addressed snapshot is corrupt")

    def _store_blob(self, data: bytes, digest: str) -> bool:
        path = self.blobs / digest
        self._verify_existing_blob(path, data, digest)
        if path.exists():
            return False
        fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=self.blobs)
        created = False
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
                created = True
            except FileExistsError:
                self._verify_existing_blob(path, data, digest)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return created

    def _maybe_cleanup_blob(self, digest: str, data: bytes, created: bool) -> None:
        if not created:
            return
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            count = con.execute(
                "SELECT count(*) FROM acquisitions WHERE sha256=?", (digest,)
            ).fetchone()[0]
            if not count:
                path = self.blobs / digest
                try:
                    if self._read_blob(path) == data:
                        path.unlink()
                except (OSError, CorruptSnapshot):
                    pass
            con.commit()

    def add(
        self,
        *,
        namespace: str,
        dataset_id: str,
        kind: str,
        source_url: str,
        provenance: str,
        license_status: str,
        data: bytes,
        format: str,
        fetched: str,
        published: str,
        observed: str,
        derived: bool = False,
        recipe: str | None = None,
        source_ids: tuple[int, ...] = (),
    ) -> str:
        """Validate and record caller-fetched bytes; return their SHA-256 digest."""
        namespace, dataset_id, kind = (
            _token(namespace, "namespace"),
            _token(dataset_id, "dataset_id"),
            _token(kind, "kind"),
        )
        source_url = _canonical_source_url(source_url)
        for value, label in ((provenance, "provenance"), (license_status, "license_status")):
            if not isinstance(value, str) or not value.strip():
                raise CatalogError(f"{label} is required")
        if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
            raise CatalogError("data must be nonempty bytes within the size limit")
        times = tuple(_time(value) for value in (fetched, published, observed))
        if derived and (not isinstance(recipe, str) or not recipe.strip()):
            raise CatalogError("derived data requires a raw-to-derived recipe")
        if not derived and (recipe is not None or source_ids):
            raise CatalogError("recipe and source references are only valid for derived data")
        if derived and not source_ids:
            raise CatalogError("derived data requires explicit acquisition source IDs")
        if len(source_ids) > 100 or any(
            type(value) is not int or value <= 0 for value in source_ids
        ):
            raise CatalogError("invalid source acquisition IDs")
        source_ids = tuple(sorted(set(source_ids)))
        shape, row_count = _shape(data, format)
        shape_json = json.dumps(shape, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(data).hexdigest()

        created = False
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                for source_id in source_ids:
                    if (
                        con.execute(
                            "SELECT 1 FROM acquisitions WHERE id=?", (source_id,)
                        ).fetchone()
                        is None
                    ):
                        raise CatalogError("source acquisition is missing")
                row = con.execute(
                    "SELECT shape FROM datasets WHERE namespace=? AND dataset_id=?",
                    (namespace, dataset_id),
                ).fetchone()
                if row and row[0] != shape_json:
                    raise ReviewRequired("dataset shape changed; review before recording")
                con.execute(
                    "INSERT OR IGNORE INTO datasets VALUES (?,?,?)",
                    (namespace, dataset_id, shape_json),
                )
                cursor = con.execute(
                    "INSERT INTO acquisitions(namespace,dataset_id,kind,sha256,source_url,"
                    "provenance,license_status,fetched,published,observed,recipe,"
                    "row_count,byte_count,format) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        namespace,
                        dataset_id,
                        kind,
                        digest,
                        source_url,
                        provenance,
                        license_status,
                        *times,
                        recipe,
                        row_count,
                        len(data),
                        format,
                    ),
                )
                if cursor.lastrowid is None:
                    raise CatalogError("acquisition identity was not assigned")
                con.executemany(
                    "INSERT INTO acquisition_sources VALUES (?,?)",
                    [(cursor.lastrowid, source_id) for source_id in source_ids],
                )
                created = self._store_blob(data, digest)
                con.commit()
        except Exception:
            self._maybe_cleanup_blob(digest, data, created)
            raise
        return digest

    def acquisitions(self, *, namespace: str | None = None, dataset_id: str | None = None):
        clauses = []
        params = []
        for column, value in (("namespace", namespace), ("dataset_id", dataset_id)):
            if value is not None:
                _token(value, column)
                clauses.append(f"a.{column} = ?")
                params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT a.*, d.shape FROM acquisitions a JOIN datasets d "
                "ON d.namespace=a.namespace AND d.dataset_id=a.dataset_id"
                + where
                + " ORDER BY a.id",
                params,
            ).fetchall()
            links = con.execute(
                "SELECT acquisition_id,source_id FROM acquisition_sources"
            ).fetchall()
        sources = {}
        for link in links:
            sources.setdefault(link["acquisition_id"], []).append(link["source_id"])
        return [
            dict(row)
            | {"shape": json.loads(row["shape"]), "source_ids": sources.get(row["id"], [])}
            for row in rows
        ]

    def snapshot(self, digest: str) -> bytes:
        """Read and verify a stored blob by its lowercase SHA-256 digest."""
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CatalogError("invalid snapshot digest")
        path = self.blobs / digest
        if not path.exists() and not path.is_symlink():
            raise CatalogError("snapshot not found")
        data = self._read_blob(path)
        if hashlib.sha256(data).hexdigest() != digest:
            raise CorruptSnapshot("snapshot digest verification failed")
        return data
