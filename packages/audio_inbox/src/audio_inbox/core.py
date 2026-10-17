from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import stat
import subprocess
import tempfile
import time
import uuid
import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import (
    AssetRef,
    Citation,
    GoalCandidate,
    JobState,
    Proposal,
    Segment,
    TranscriptReply,
    TranscriptVersion,
)
from .validation import valid_goal, validate_transcript

__all__ = [
    "Segment",
    "TranscriptReply",
    "Citation",
    "GoalCandidate",
    "AssetRef",
    "TranscriptVersion",
    "Proposal",
    "JobState",
    "AudioInbox",
    "RefusedNotAcceptedError",
]


class RefusedNotAcceptedError(RuntimeError):
    """Safe-to-retry refusal: provider confirms request was not accepted."""


class AudioInbox:
    def __init__(
        self,
        *,
        root: Path,
        inbox: Path,
        transcriber: Callable[[AssetRef], TranscriptReply],
        suggester: Callable[[TranscriptVersion], list[GoalCandidate]],
        clock: Callable[[], float] | None = None,
        settle_seconds: float = 2.0,
        max_file_bytes: int = 100 * 1024 * 1024,
        max_total_bytes: int = 1024 * 1024 * 1024,
        max_duration_seconds: float = 2 * 60 * 60,
        max_text_chars: int = 200_000,
        max_safe_retries: int = 1,
    ):
        self.root = Path(root)
        self.inbox = Path(inbox)
        if any(
            path.is_symlink()
            for path in (self.root, self.inbox, self.root / "assets", self.root / "inbox.sqlite3")
        ):
            raise ValueError("unsafe_store_path")
        self.transcriber = transcriber
        self.suggester = suggester
        self.clock = clock or time.time

        self.settle_seconds = self._num("settle_seconds", settle_seconds, min_value=0)
        self.max_file_bytes = int(self._num("max_file_bytes", max_file_bytes, min_value=1))
        self.max_total_bytes = int(self._num("max_total_bytes", max_total_bytes, min_value=1))
        self.max_duration_seconds = self._num(
            "max_duration_seconds", max_duration_seconds, min_value=0
        )
        self.max_text_chars = int(self._num("max_text_chars", max_text_chars, min_value=1))
        self.max_safe_retries = int(self._num("max_safe_retries", max_safe_retries, min_value=0))

        self.root.mkdir(parents=True, exist_ok=True)
        self._chmod_private(self.root)
        (self.root / "assets").mkdir(exist_ok=True)
        self._chmod_private(self.root / "assets")
        self.db_path = self.root / "inbox.sqlite3"
        self._init_db()
        self._recover_running_jobs()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    sha256 TEXT UNIQUE NOT NULL,
                    media_type TEXT NOT NULL,
                    duration REAL NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    stored_path TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_key TEXT UNIQUE NOT NULL,
                    asset_id TEXT,
                    status TEXT NOT NULL,
                    validation_error TEXT,
                    version_id TEXT,
                    discovered_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    running_pid INTEGER,
                    claim_token TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    safe_retry_count INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(asset_id) REFERENCES assets(id),
                    FOREIGN KEY(version_id) REFERENCES versions(id)
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    parent_version_id TEXT,
                    model TEXT NOT NULL,
                    text TEXT NOT NULL,
                    segments_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id),
                    FOREIGN KEY(asset_id) REFERENCES assets(id)
                );
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY,
                    version_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    action TEXT NOT NULL,
                    due_date TEXT,
                    uncertainty TEXT,
                    citations_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reviewed_title TEXT,
                    reviewed_action TEXT,
                    attached_ref TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(version_id) REFERENCES versions(id)
                );
                CREATE TABLE IF NOT EXISTS suggestion_runs (
                    version_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(version_id) REFERENCES versions(id)
                );
                """
            )

    def _recover_running_jobs(self) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, running_pid FROM jobs WHERE status='running'"
            ).fetchall()
            for row in rows:
                pid = row["running_pid"]
                if pid is None or not self._pid_alive(int(pid)):
                    conn.execute(
                        "UPDATE jobs SET status='uncertain', running_pid=NULL, claim_token"
                        "=NULL, validation_error='stale_running', updated_at=? WHERE id=?",
                        (self.clock(), row["id"]),
                    )

    def _pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "posix":
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
        return pid == os.getpid()

    def scan(self) -> list[str]:
        self.inbox.mkdir(parents=True, exist_ok=True)
        ids: list[str] = []
        total = self._accepted_total_bytes()
        for entry in sorted(self.inbox.iterdir(), key=lambda p: p.name):
            st = self._safe_stat(entry)
            if st is None or not self._is_stable_stat(st):
                continue
            source_key = self._source_key(entry.name, st)
            existing = self._job_by_source_key(source_key)
            if existing:
                ids.append(existing["id"])
                continue

            now = self.clock()
            job_id = self._new_id("job")
            size = int(st.st_size)
            if size > self.max_file_bytes:
                self._insert_job(
                    job_id, entry.name, source_key, None, "invalid", "file_too_large", now
                )
                ids.append(job_id)
                continue
            if total + size > self.max_total_bytes:
                self._insert_job(
                    job_id, entry.name, source_key, None, "invalid", "total_limit_exceeded", now
                )
                ids.append(job_id)
                continue

            ok, media_type, duration, err = self._probe_media(entry, st)
            if not ok:
                self._insert_job(job_id, entry.name, source_key, None, "invalid", err, now)
                ids.append(job_id)
                continue
            if duration > self.max_duration_seconds:
                self._insert_job(
                    job_id, entry.name, source_key, None, "invalid", "duration_too_long", now
                )
                ids.append(job_id)
                continue

            digest = self._hash_file(entry, st)
            asset_id = self._get_or_create_asset(digest, entry, st, media_type, duration, size)
            self._insert_job(job_id, entry.name, source_key, asset_id, "queued", None, now)
            ids.append(job_id)
            total += size
        return ids

    def _accepted_total_bytes(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(a.size_bytes),0) AS n FROM jobs j JOIN assets"
                " a ON a.id=j.asset_id WHERE j.status!='invalid'"
            ).fetchone()
            return int(row["n"])

    def _safe_stat(self, path: Path) -> os.stat_result | None:
        try:
            value = path.lstat()
            return value if stat.S_ISREG(value.st_mode) else None
        except OSError:
            return None

    def _is_stable_stat(self, st: os.stat_result) -> bool:
        return (self.clock() - st.st_mtime) >= self.settle_seconds

    def _source_key(self, name: str, st: os.stat_result) -> str:
        raw = f"{name}|{st.st_dev}|{st.st_ino}|{st.st_size}|{st.st_mtime_ns}".encode()
        return hashlib.sha256(raw).hexdigest()

    def _probe_media(
        self, path: Path, expected: os.stat_result
    ) -> tuple[bool, str, float, str | None]:
        suffix = path.suffix.lower()
        if suffix == ".wav":
            try:
                with self._open_verified(path, expected) as f:
                    with wave.open(f, "rb") as wav:
                        if wav.getcomptype() != "NONE":
                            return False, "audio/wav", 0.0, "unsupported_wav_encoding"
                        channels = wav.getnchannels()
                        width = wav.getsampwidth()
                        rate = wav.getframerate()
                        frames = wav.getnframes()
                        if channels <= 0 or channels > 8:
                            return False, "audio/wav", 0.0, "invalid_channels"
                        if width <= 0 or width > 4:
                            return False, "audio/wav", 0.0, "invalid_sample_width"
                        if rate < 8000 or rate > 192000:
                            return False, "audio/wav", 0.0, "invalid_sample_rate"
                        declared_pcm = frames * channels * width
                        if declared_pcm <= 0 or declared_pcm > expected.st_size:
                            return False, "audio/wav", 0.0, "truncated_wav"
                        remaining = frames
                        actual = 0
                        while remaining:
                            count = min(remaining, 8192)
                            chunk = wav.readframes(count)
                            actual += len(chunk)
                            remaining -= count
                            if len(chunk) != count * channels * width:
                                return False, "audio/wav", 0.0, "truncated_wav"
                        duration = frames / float(rate)
                        if not math.isfinite(duration):
                            return False, "audio/wav", 0.0, "invalid_duration"
                        return True, "audio/wav", duration, None
            except Exception:
                return False, "audio/wav", 0.0, "malformed_wav"

        mapping = {
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".flac": "audio/flac",
            ".ogg": "audio/ogg",
        }
        if suffix in mapping:
            return self._probe_ffprobe(path, mapping[suffix])
        return False, "application/octet-stream", 0.0, "unsupported_format"

    def _probe_ffprobe(self, path: Path, media_type: str) -> tuple[bool, str, float, str | None]:
        argv = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,channels,sample_rate",
            "-of",
            "json",
            str(path),
        ]
        try:
            cp = subprocess.run(argv, capture_output=True, text=True, timeout=3, check=False)
            if cp.returncode != 0 or len(cp.stdout) > 64_000:
                return False, media_type, 0.0, "probe_failed"
            data = json.loads(cp.stdout)
            if not any(stream.get("codec_type") == "audio" for stream in data.get("streams", [])):
                return False, media_type, 0.0, "audio_stream_missing"
            duration = float(data.get("format", {}).get("duration", "nan"))
            if not math.isfinite(duration) or duration < 0:
                return False, media_type, 0.0, "invalid_duration"
            return True, media_type, duration, None
        except Exception:
            return False, media_type, 0.0, "probe_failed"

    @contextmanager
    def _open_verified(self, path: Path, expected: os.stat_result):
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) != (
                expected.st_dev,
                expected.st_ino,
                expected.st_size,
                expected.st_mtime_ns,
            ):
                raise RuntimeError("source_changed")
            with os.fdopen(fd, "rb", closefd=False) as f:
                yield f
        finally:
            os.close(fd)

    def _hash_file(self, path: Path, expected: os.stat_result) -> str:
        h = hashlib.sha256()
        with self._open_verified(path, expected) as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _get_or_create_asset(
        self,
        digest: str,
        source: Path,
        src_stat: os.stat_result,
        media_type: str,
        duration: float,
        size: int,
    ) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM assets WHERE sha256=?", (digest,)).fetchone()
            if row:
                return str(row["id"])
            asset_id = self._new_id("asset")
            suffix = source.suffix.lower() or ".bin"
            out = self.root / "assets" / f"{asset_id}{suffix}"
            try:
                hasher = hashlib.sha256()
                copied = 0
                fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as dst, self._open_verified(source, src_stat) as src:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > self.max_file_bytes:
                            raise ValueError("file_too_large")
                        hasher.update(chunk)
                        dst.write(chunk)
                if copied != size or hasher.hexdigest() != digest:
                    raise RuntimeError("copy_mismatch")
                conn.execute(
                    "INSERT INTO assets(id, sha256, media_type, duration, size_bytes, "
                    "stored_path) VALUES(?,?,?,?,?,?)",
                    (asset_id, digest, media_type, duration, size, str(out)),
                )
                return asset_id
            except Exception:
                if out.exists():
                    try:
                        out.unlink()
                    except OSError:
                        pass
                raise

    def _insert_job(
        self,
        job_id: str,
        source_name: str,
        source_key: str,
        asset_id: str | None,
        status: str,
        error: str | None,
        now: float,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs(id, source_name, source_key, asset_id, status, v"
                "alidation_error, discovered_at, updated_at) VALUES(?,?,?,?,?,?,?,"
                "?)",
                (job_id, source_name, source_key, asset_id, status, error, now, now),
            )

    def _job_by_source_key(self, source_key: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM jobs WHERE source_key=?", (source_key,)).fetchone()

    def jobs(self) -> list[JobState]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY discovered_at, id").fetchall()
        return [
            JobState(
                id=r["id"],
                source_name=r["source_name"],
                status=r["status"],
                asset_id=r["asset_id"],
                version_id=r["version_id"],
                error=r["validation_error"],
            )
            for r in rows
        ]

    def process(self, job_id: str) -> str:
        job = self._get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["status"] == "completed":
            return str(job["version_id"])
        if job["status"] in {"invalid", "cancelled", "uncertain", "failed"}:
            raise RuntimeError(f"job not processable: {job['status']}")

        claim = self._claim_running(job_id)
        if claim is None:
            raise RuntimeError("job already running")

        try:
            asset = self._asset_for_job(job_id)
            reply = self._validate_reply(self.transcriber(asset), asset.duration)
            version_id = self._complete_with_version(job_id, claim, asset.id, reply)
            if version_id is None:
                raise RuntimeError("job cancelled")
            return version_id
        except TimeoutError:
            self._set_claim_status(job_id, claim, "uncertain", "provider_timeout")
            raise
        except RefusedNotAcceptedError:
            current = self._get_job(job_id)
            retries = int(current["safe_retry_count"]) if current else 0
            if retries < self.max_safe_retries:
                self._set_claim_retryable(job_id, claim, retries + 1)
            else:
                self._set_claim_status(job_id, claim, "failed", "provider_refused")
            raise
        except Exception:
            self._set_claim_status(job_id, claim, "failed", "provider_error")
            raise

    def cancel(self, job_id: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row["status"] in {"completed", "invalid"}:
                return
            conn.execute(
                "UPDATE jobs SET status='cancelled', validation_error=NULL, runnin"
                "g_pid=NULL, claim_token=NULL, updated_at=? WHERE id=?",
                (self.clock(), job_id),
            )

    def _claim_running(self, job_id: str) -> str | None:
        token = self._new_id("claim")
        now = self.clock()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row["status"] != "queued":
                return None
            conn.execute(
                "UPDATE jobs SET status='running', running_pid=?, claim_token=?, u"
                "pdated_at=?, attempt_count=attempt_count+1 WHERE id=? AND status="
                "'queued'",
                (os.getpid(), token, now, job_id),
            )
            return token

    def _set_claim_retryable(self, job_id: str, claim: str, safe_retry_count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='queued', safe_retry_count=?, validation_e"
                "rror='provider_refused', running_pid=NULL, claim_token=NULL, upda"
                "ted_at=? WHERE id=? AND status='running' AND claim_token=?",
                (safe_retry_count, self.clock(), job_id, claim),
            )

    def _set_claim_status(self, job_id: str, claim: str, status: str, error: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, validation_error=?, running_pid=NULL, c"
                "laim_token=NULL, updated_at=? WHERE id=? AND status='running' AND"
                " claim_token=?",
                (status, error, self.clock(), job_id, claim),
            )

    def _complete_with_version(
        self, job_id: str, claim: str, asset_id: str, reply: TranscriptReply
    ) -> str | None:
        version_id = self._new_id("v")
        payload = json.dumps([asdict(s) for s in reply.segments], separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, claim_token FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row or row["status"] != "running" or row["claim_token"] != claim:
                return None
            conn.execute(
                "INSERT INTO versions(id, job_id, asset_id, parent_version_id, mod"
                "el, text, segments_json, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    job_id,
                    asset_id,
                    None,
                    reply.model,
                    reply.text,
                    payload,
                    self.clock(),
                ),
            )
            conn.execute(
                "UPDATE jobs SET status='completed', version_id=?, running_pid=NUL"
                "L, claim_token=NULL, validation_error=NULL, updated_at=? WHERE id"
                "=? AND claim_token=?",
                (version_id, self.clock(), job_id, claim),
            )
            return version_id

    def _get_job(self, job_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def _asset_ref_from_row(self, row: sqlite3.Row) -> AssetRef:
        reference = AssetRef(
            id=row["id"],
            path=Path(row["stored_path"]),
            sha256=row["sha256"],
            media_type=row["media_type"],
            duration=float(row["duration"]),
            size_bytes=int(row["size_bytes"]),
        )
        expected = self._safe_stat(reference.path)
        if expected is None or reference.path.parent.resolve() != (self.root / "assets").resolve():
            raise ValueError("asset_integrity_failed")
        if (
            expected.st_size != reference.size_bytes
            or self._hash_file(reference.path, expected) != reference.sha256
        ):
            raise ValueError("asset_integrity_failed")
        return reference

    def _asset_for_job(self, job_id: str) -> AssetRef:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT a.* FROM jobs j JOIN assets a ON a.id=j.asset_id WHERE j.id=?", (job_id,)
            ).fetchone()
        if not row:
            raise RuntimeError("no asset for job")
        return self._asset_ref_from_row(row)

    def _validate_reply(self, reply: TranscriptReply, duration: float) -> TranscriptReply:
        return validate_transcript(reply, duration, self.max_text_chars)

    def transcript(self, version_id: str) -> TranscriptVersion:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
        if not row:
            raise KeyError(version_id)
        segments = [Segment(**seg) for seg in json.loads(row["segments_json"])]
        return TranscriptVersion(
            id=row["id"],
            job_id=row["job_id"],
            parent_version_id=row["parent_version_id"],
            model=row["model"],
            text=row["text"],
            segments=segments,
        )

    def correction(self, parent_version_id: str, reply: TranscriptReply) -> str:
        parent = self.transcript(parent_version_id)
        with self._connect() as conn:
            j = conn.execute(
                "SELECT asset_id FROM versions WHERE id=?", (parent_version_id,)
            ).fetchone()
            if not j:
                raise KeyError(parent_version_id)
            asset_id = str(j["asset_id"])
        asset = self._asset_by_id(asset_id)
        validated = self._validate_reply(reply, asset.duration)
        version_id = self._new_id("v")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO versions(id, job_id, asset_id, parent_version_id, mod"
                "el, text, segments_json, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    parent.job_id,
                    asset_id,
                    parent_version_id,
                    validated.model,
                    validated.text,
                    json.dumps([asdict(s) for s in validated.segments], separators=(",", ":")),
                    self.clock(),
                ),
            )
        return version_id

    def _asset_by_id(self, asset_id: str) -> AssetRef:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            raise KeyError(asset_id)
        return self._asset_ref_from_row(row)

    def proposals(self, version_id: str) -> list[Proposal]:
        existing = self._proposal_rows(version_id)
        if existing:
            return [self._proposal_from_row(r) for r in existing]

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT * FROM suggestion_runs WHERE version_id=?", (version_id,)
            ).fetchone()
            if run:
                if run["status"] == "completed":
                    return [self._proposal_from_row(row) for row in self._proposal_rows(version_id)]
                raise RuntimeError("suggestions unavailable")
            conn.execute(
                "INSERT INTO suggestion_runs VALUES (?, 'running', NULL, ?)",
                (version_id, self.clock()),
            )

        transcript = self.transcript(version_id)
        try:
            candidates = self.suggester(transcript)
        except TimeoutError:
            self._mark_suggestion_run(version_id, "uncertain", "suggestion_timeout")
            raise
        except Exception:
            self._mark_suggestion_run(version_id, "failed", "suggestion_error")
            raise

        if not isinstance(candidates, list) or len(candidates) > 100:
            self._mark_suggestion_run(version_id, "failed", "suggestion_shape")
            raise ValueError("invalid_suggestion_shape")
        duration = self._asset_for_version(version_id).duration
        valid = [c for c in candidates if valid_goal(c, transcript, duration, self.max_text_chars)]
        now = self.clock()
        with self._connect() as conn:
            for cand in valid:
                conn.execute(
                    "INSERT INTO proposals(id, version_id, title, action, due_date, un"
                    "certainty, citations_json, status, created_at, updated_at) VALUES"
                    "(?,?,?,?,?,?,?,?,?,?)",
                    (
                        self._new_id("p"),
                        version_id,
                        cand.title,
                        cand.action,
                        cand.due_date,
                        cand.uncertainty,
                        json.dumps([asdict(c) for c in cand.citations], separators=(",", ":")),
                        "pending",
                        now,
                        now,
                    ),
                )
            conn.execute(
                "INSERT OR REPLACE INTO suggestion_runs(version_id, status, error_"
                "code, updated_at) VALUES(?,?,?,?)",
                (version_id, "completed", None, now),
            )
        return [self._proposal_from_row(r) for r in self._proposal_rows(version_id)]

    def _suggestion_run(self, version_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM suggestion_runs WHERE version_id=?", (version_id,)
            ).fetchone()

    def _mark_suggestion_run(self, version_id: str, status: str, error_code: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO suggestion_runs(version_id, status, error_"
                "code, updated_at) VALUES(?,?,?,?)",
                (version_id, status, error_code, self.clock()),
            )

    def _asset_for_version(self, version_id: str) -> AssetRef:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT a.* FROM versions v JOIN assets a ON a.id=v.asset_id WHERE v.id=?",
                (version_id,),
            ).fetchone()
        if not row:
            raise KeyError(version_id)
        return self._asset_ref_from_row(row)

    def review(
        self,
        proposal_id: str,
        decision: str,
        *,
        edited_title: str | None = None,
        edited_action: str | None = None,
    ) -> Proposal:
        row = self._proposal_by_id(proposal_id)
        if row is None:
            raise KeyError(proposal_id)
        decision = decision.lower()
        if decision not in {"approve", "reject", "edit"}:
            raise ValueError("decision must be approve/reject/edit")
        status = "approved" if decision in {"approve", "edit"} else "rejected"
        rt = edited_title if decision == "edit" else None
        ra = edited_action if decision == "edit" else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE proposals SET status=?, reviewed_title=?, reviewed_action="
                "?, updated_at=? WHERE id=?",
                (status, rt, ra, self.clock(), proposal_id),
            )
        updated = self._proposal_by_id(proposal_id)
        if updated is None:
            raise KeyError(proposal_id)
        return self._proposal_from_row(updated)

    def attach_approved(self, proposal_id: str, external_ref: str = "fake") -> str:
        row = self._proposal_by_id(proposal_id)
        if row is None:
            raise KeyError(proposal_id)
        if row["status"] != "approved":
            raise RuntimeError("proposal not approved")
        if row["attached_ref"]:
            return str(row["attached_ref"])
        with self._connect() as conn:
            conn.execute(
                "UPDATE proposals SET attached_ref=?, updated_at=? WHERE id=?",
                (external_ref, self.clock(), proposal_id),
            )
        return external_ref

    def _proposal_rows(self, version_id: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM proposals WHERE version_id=? ORDER BY created_at, id", (version_id,)
            ).fetchall()

    def _proposal_by_id(self, proposal_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()

    def _proposal_from_row(self, row: sqlite3.Row) -> Proposal:
        title = row["reviewed_title"] or row["title"]
        action = row["reviewed_action"] or row["action"]
        citations = [Citation(**c) for c in json.loads(row["citations_json"])]
        return Proposal(
            id=row["id"],
            version_id=row["version_id"],
            title=title,
            action=action,
            citations=citations,
            status=row["status"],
            due_date=row["due_date"],
            uncertainty=row["uncertainty"],
        )

    def export(self, version_id: str, destination: Path) -> Path:
        destination = Path(destination)
        if ".." in destination.parts:
            raise ValueError("unsafe destination")
        transcript = self.transcript(version_id)
        approved = [
            self._proposal_from_row(row)
            for row in self._proposal_rows(version_id)
            if row["status"] == "approved"
        ]

        lines = [
            f"# Transcript {transcript.id}",
            "",
            f"Source job: {transcript.job_id}",
            "",
            "## Text",
            "",
            transcript.text,
            "",
            "## Segments",
            "",
        ]
        for s in transcript.segments:
            if s.start is not None and s.end is not None:
                lines.append(f"- [{s.start:.2f}-{s.end:.2f}] ({s.segment_id}) {s.text}")
            else:
                lines.append(f"- ({s.segment_id}) {s.text}")
        lines.extend(["", "## Approved goals", ""])
        for p in approved:
            lines.append(f"- {p.title}: {p.action}")
            for c in p.citations:
                t = (
                    f" [{c.start:.2f}-{c.end:.2f}]"
                    if c.start is not None and c.end is not None
                    else ""
                )
                lines.append(f'  - cite {c.segment_id}{t}: "{c.quote}"')
        body = "\n".join(lines) + "\n"

        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-export-", suffix=".md", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
            if os.name == "posix":
                os.chmod(tmp, 0o600)
            os.replace(tmp, destination)
            if os.name == "posix":
                os.chmod(destination, 0o600)
            return destination
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _chmod_private(path: Path) -> None:
        if os.name == "posix":
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass

    @staticmethod
    def _num(name: str, value: Any, *, min_value: float = 0.0) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be numeric")
        x = float(value)
        if not math.isfinite(x) or x < min_value:
            raise ValueError(f"{name} out of range")
        return x
