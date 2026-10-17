from __future__ import annotations

import errno
import json
import math
import os
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import Any

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "needs_review", "uncertain"}
ALLOWED_REQUEUE_REASONS = {"not_accepted", "provider_refused", "rate_limited"}
UNKNOWN_OUTCOME_STATUSES = {"needs_review", "uncertain"}

BASE_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS controls (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_id INTEGER,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  created_day TEXT NOT NULL,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  provider TEXT,
  deployment TEXT,
  role TEXT,
  expected_deliverable TEXT,
  allowed_paths_json TEXT NOT NULL DEFAULT '[]',
  worker_id TEXT,
  owner_pid INTEGER,
  claim_token TEXT,
  claimed_identity TEXT,
  claimed_at REAL,
  retry_after REAL,
  reserved_tokens INTEGER NOT NULL DEFAULT 0,
  actual_tokens INTEGER NOT NULL DEFAULT 0,
  cache_fingerprint TEXT,
  spec_json TEXT NOT NULL,
  FOREIGN KEY(parent_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  ts REAL NOT NULL,
  type TEXT NOT NULL,
  data_json TEXT NOT NULL,
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  data_json TEXT NOT NULL,
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS dispatch_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  claim_token TEXT NOT NULL,
  run_identity TEXT NOT NULL,
  dispatch_day TEXT NOT NULL,
  reserved_estimate INTEGER NOT NULL,
  actual_tokens INTEGER,
  status TEXT NOT NULL,
  created_at REAL NOT NULL,
  finished_at REAL,
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
"""


class JobQueue:
    def __init__(
        self,
        db_path,
        *,
        provider_deployments: dict[str, str] | None = None,
        identity: str = "queue",
        max_global: int = 2,
        max_retry_revisions: int = 3,
        run_token_cap: int = 20000,
        day_token_cap: int = 200000,
        provider_daily_claim_cap: dict[str, int] | None = None,
        per_minute_request_limit: int | None = None,
        per_minute_token_limit: int | None = None,
        enable_explicit_cache: bool = False,
        clock: Callable[[], float] | None = None,
        pid_fn: Callable[[], int] | None = None,
        is_pid_alive: Callable[[int], bool] | None = None,
    ):
        self.db_path = os.fspath(db_path)
        self.provider_deployments = dict(provider_deployments or {})
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", identity):
            raise ValueError("invalid_identity")
        self.identity = identity
        self.max_global = self._require_int_in_range("max_global", max_global, 1, 2)
        self.max_retry_revisions = self._require_int_in_range(
            "max_retry_revisions", max_retry_revisions, 1, 20
        )
        self.run_token_cap = self._require_int_in_range("run_token_cap", run_token_cap, 1, 10**9)
        self.day_token_cap = self._require_int_in_range("day_token_cap", day_token_cap, 1, 10**9)
        self.provider_daily_claim_cap = {
            str(k): self._require_int_in_range(f"provider_daily_claim_cap[{k}]", v, 1, 10**9)
            for k, v in (provider_daily_claim_cap or {}).items()
        }
        self.per_minute_request_limit = (
            None
            if per_minute_request_limit is None
            else self._require_int_in_range(
                "per_minute_request_limit", per_minute_request_limit, 1, 10**6
            )
        )
        self.per_minute_token_limit = (
            None
            if per_minute_token_limit is None
            else self._require_int_in_range(
                "per_minute_token_limit", per_minute_token_limit, 1, 10**9
            )
        )
        self.enable_explicit_cache = bool(enable_explicit_cache)
        self._clock = clock or time.time
        self._pid_fn = pid_fn or os.getpid
        self._is_pid_alive = is_pid_alive or self._default_pid_alive

        if os.path.islink(self.db_path):
            raise ValueError("database_symlink")
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        if not os.path.exists(self.db_path):
            open(self.db_path, "a", encoding="utf-8").close()
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass
        self._init_db()

    @staticmethod
    def _require_int_in_range(name: str, value: Any, lo: int, hi: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < lo or value > hi:
            raise ValueError(f"invalid_{name}")
        return int(value)

    @staticmethod
    def _default_pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError as exc:
            return exc.errno != errno.ESRCH

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=2.0)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript(BASE_SCHEMA)
            c.execute("INSERT OR IGNORE INTO controls(key,value) VALUES('paused','0')")
            self._migrate_jobs_table(c)
            self._create_indexes(c)

    def _create_indexes(self, c: sqlite3.Connection) -> None:
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_retry ON jobs(status,r"
            "etry_after,priority,id)"
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_running ON jobs(status,deployment)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_cache ON jobs(status,cache_fingerprint,id)")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_attempts_day ON dispatch_attempts(dispatch_day,status)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_attempts_run ON dispatch_attempts("
            "run_identity,dispatch_day,status)"
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_attempts_time ON dispatch_attempts(created_at)")

    def _migrate_jobs_table(self, c: sqlite3.Connection) -> None:
        existing = {row[1] for row in c.execute("PRAGMA table_info(jobs)")}
        needed = {
            "parent_id": "INTEGER",
            "created_day": "TEXT NOT NULL DEFAULT ''",
            "priority": "INTEGER NOT NULL DEFAULT 0",
            "provider": "TEXT",
            "deployment": "TEXT",
            "role": "TEXT",
            "expected_deliverable": "TEXT",
            "allowed_paths_json": "TEXT NOT NULL DEFAULT '[]'",
            "owner_pid": "INTEGER",
            "claim_token": "TEXT",
            "claimed_identity": "TEXT",
            "claimed_at": "REAL",
            "reserved_tokens": "INTEGER NOT NULL DEFAULT 0",
            "actual_tokens": "INTEGER NOT NULL DEFAULT 0",
            "retry_after": "REAL",
            "cache_fingerprint": "TEXT",
        }
        for col, decl in needed.items():
            if col not in existing:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {decl}")
        now_day = time.strftime("%Y-%m-%d", time.gmtime(self._clock()))
        c.execute("UPDATE jobs SET created_day=? WHERE COALESCE(created_day,'')=''", (now_day,))
        c.execute(
            "UPDATE jobs SET allowed_paths_json='[]' WHERE COALESCE(allowed_paths_json,'')=''"
        )
        c.execute("UPDATE jobs SET priority=0 WHERE priority IS NULL")
        for row in c.execute("SELECT id,spec_json,provider,deployment FROM jobs").fetchall():
            spec = json.loads(row["spec_json"])
            provider = row["provider"] or spec.get("provider")
            deployment = row["deployment"] or self.provider_deployments.get(provider, provider)
            c.execute(
                "UPDATE jobs SET provider=?,deployment=? WHERE id=?",
                (provider, deployment, row["id"]),
            )
        self._backfill_attempts(c)

    def _backfill_attempts(self, c: sqlite3.Connection) -> None:
        rows = c.execute(
            "SELECT * FROM jobs WHERE (attempts>0 OR status='running') AND NOT EXISTS "
            "(SELECT 1 FROM dispatch_attempts WHERE dispatch_attempts.job_id=jobs.id)"
        ).fetchall()
        for row in rows:
            count = max(1, int(row["attempts"]))
            if count > 1000:
                raise ValueError("invalid_legacy_attempt_count")
            try:
                reserved = self._validate_spec(json.loads(row["spec_json"]))[6]
            except (ValueError, TypeError):
                reserved = len(row["spec_json"].encode()) + 8192
            timestamp = row["claimed_at"] or row["updated_at"] or row["created_at"]
            day = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
            for attempt in range(count):
                c.execute(
                    "INSERT INTO dispatch_attempts(job_id,claim_token,run_identity,dispatch_day,"
                    "reserved_estimate,status,created_at,finished_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        row["id"],
                        f"migrated-{row['id']}-{attempt}",
                        "migrated",
                        day,
                        reserved,
                        "uncertain",
                        timestamp,
                        timestamp,
                    ),
                )

    def _event(self, c: sqlite3.Connection, job_id: int, typ: str, data: dict[str, Any]) -> None:
        c.execute(
            "INSERT INTO events(job_id,ts,type,data_json) VALUES(?,?,?,?)",
            (job_id, self._clock(), typ, json.dumps(data, separators=(",", ":"), sort_keys=True)),
        )

    def _sanitize_paths(self, paths: Any) -> list[str]:
        if paths is None:
            return []
        if not isinstance(paths, list):
            raise ValueError("invalid_allowed_paths")
        out: list[str] = []
        for p in paths:
            if not isinstance(p, str) or not p or len(p) > 240 or "\\" in p:
                raise ValueError("invalid_allowed_paths")
            pp = PurePosixPath(p)
            if (
                pp.is_absolute()
                or ".." in pp.parts
                or not pp.parts
                or str(pp) != p
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in p)
            ):
                raise ValueError("invalid_allowed_paths")
            out.append(str(pp))
        if len(out) != len(set(out)):
            raise ValueError("duplicate_allowed_path")
        return sorted(out)

    def _canonical_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(spec, sort_keys=True, separators=(",", ":")))

    def _resolve_provider(self, provider: str) -> str:
        if self.provider_deployments and provider not in self.provider_deployments:
            raise ValueError("unknown_provider")
        return self.provider_deployments.get(provider, provider)

    def _validate_spec(
        self, spec: dict[str, Any]
    ) -> tuple[dict[str, Any], str, str, int, float, list[str], int, str, str, int]:
        req = {"provider", "topic", "source_excerpts", "max_output_tokens", "deadline"}
        if not req.issubset(spec):
            raise ValueError("missing_required_spec_fields")
        provider = spec["provider"]
        if not isinstance(provider, str) or not provider:
            raise ValueError("invalid_provider")
        deployment = self._resolve_provider(provider)
        topic = spec["topic"]
        if not isinstance(topic, str) or not 1 <= len(topic) <= 4000:
            raise ValueError("invalid_topic")
        mot = spec["max_output_tokens"]
        if isinstance(mot, bool) or not isinstance(mot, int) or not 64 <= mot <= 8192:
            raise ValueError("invalid_output_budget")
        deadline = spec["deadline"]
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
        ):
            raise ValueError("invalid_deadline")
        if not 0 < float(deadline) <= 180:
            raise ValueError("invalid_deadline")
        src = spec["source_excerpts"]
        if not isinstance(src, list) or not src:
            raise ValueError("source_excerpts_must_be_list")
        ids: list[str] = []
        char_budget = 0
        for source in src:
            if (
                not isinstance(source, dict)
                or not isinstance(source.get("source_id"), str)
                or not source["source_id"]
                or not isinstance(source.get("text"), str)
            ):
                raise ValueError("invalid_source")
            ids.append(source["source_id"])
            char_budget += len(source["text"])
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_source_id")
        if len(json.dumps(spec).encode("utf-8")) > 100_000:
            raise ValueError("invalid_source_budget")
        role = spec.get("role", "user")
        expected = spec.get("expected_deliverable", "json")
        priority = spec.get("priority", 0)
        if not isinstance(role, str) or not 1 <= len(role) <= 64:
            raise ValueError("invalid_role")
        if not isinstance(expected, str) or expected not in {"json", "coding_proposal"}:
            raise ValueError("invalid_expected_deliverable")
        if (
            isinstance(priority, bool)
            or not isinstance(priority, int)
            or not -100 <= priority <= 100
        ):
            raise ValueError("invalid_priority")
        paths = self._sanitize_paths(spec.get("allowed_proposal_paths", []))
        if expected == "coding_proposal" and not paths:
            raise ValueError("missing_allowed_paths")
        reserved = (
            len(json.dumps(spec["source_excerpts"]).encode()) + len(topic.encode()) + 512 + mot
        )
        if reserved <= 0 or reserved > 10**8:
            raise ValueError("invalid_estimated_tokens")
        canonical = self._canonical_spec(spec)
        return (
            canonical,
            provider,
            deployment,
            mot,
            float(deadline),
            paths,
            reserved,
            role,
            expected,
            priority,
        )

    def _cache_fingerprint(
        self, spec: dict[str, Any], deployment: str, paths: list[str], role: str, expected: str
    ) -> str:
        payload = {
            "provider": spec.get("provider"),
            "deployment": deployment,
            "topic": spec.get("topic"),
            "source_excerpts": spec.get("source_excerpts"),
            "recipe": spec.get("recipe"),
            "role": role,
            "expected_deliverable": expected,
            "allowed_proposal_paths": paths,
            "max_output_tokens": spec.get("max_output_tokens"),
            "deadline": spec.get("deadline"),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _usage_charge_sql(self) -> str:
        return (
            "CASE "
            "WHEN status='running' OR actual_tokens IS NULL THEN reserved_estimate "
            "WHEN status IN ('uncertain','needs_review') "
            "THEN MAX(COALESCE(actual_tokens,0),reserved_estimate) "
            "ELSE COALESCE(actual_tokens,0) END"
        )

    def _sum_tokens(
        self,
        c: sqlite3.Connection,
        *,
        day: str | None = None,
        run_identity: str | None = None,
        since: float | None = None,
    ) -> int:
        where = []
        args: list[Any] = []
        if day is not None:
            where.append("dispatch_day=?")
            args.append(day)
        if run_identity is not None:
            where.append("run_identity=?")
            args.append(run_identity)
        if since is not None:
            where.append("created_at>=?")
            args.append(since)
        predicate = " AND ".join(where) or "1=1"
        sql = (
            f"SELECT COALESCE(SUM({self._usage_charge_sql()}),0) "
            f"FROM dispatch_attempts WHERE {predicate}"
        )
        row = c.execute(sql, tuple(args)).fetchone()
        return int(row[0] or 0)

    def _sum_requests(self, c: sqlite3.Connection, *, since: float) -> int:
        row = c.execute(
            "SELECT COUNT(1) FROM dispatch_attempts WHERE created_at>=?", (since,)
        ).fetchone()
        return int(row[0] or 0)

    def _explicit_cache_copy(
        self,
        c: sqlite3.Connection,
        spec: dict[str, Any],
        fingerprint: str,
        provider: str,
        deployment: str,
        role: str,
        expected: str,
        paths: list[str],
        reserved: int,
        priority: int,
    ) -> int | None:
        if not (self.enable_explicit_cache and spec.get("cache_lookup")):
            return None
        rows = c.execute(
            "SELECT id FROM jobs WHERE status='completed' AND cache_fingerprint=? ORDER BY id DESC",
            (fingerprint,),
        ).fetchall()
        if not rows:
            return None
        now = self._clock()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        source_id = int(rows[0]["id"])
        cur = c.execute(
            "INSERT INTO jobs(parent_id,created_at,updated_at,created_day,stat"
            "us,priority,provider,deployment,role,"
            "expected_deliverable,allowed_paths_json,reserved_tokens,actual_to"
            "kens,cache_fingerprint,spec_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                now,
                now,
                day,
                "completed",
                priority,
                provider,
                deployment,
                role,
                expected,
                json.dumps(paths),
                reserved,
                0,
                fingerprint,
                json.dumps(spec, separators=(",", ":"), sort_keys=True),
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("missing_job_id")
        jid = cur.lastrowid
        placeholders = ",".join("?" for _ in rows)
        artifact = c.execute(
            f"SELECT kind,data_json FROM artifacts WHERE job_id IN ({placeholders}) "
            "ORDER BY id DESC LIMIT 1",
            tuple(int(r["id"]) for r in rows),
        ).fetchone()
        if artifact:
            c.execute(
                "INSERT INTO artifacts(job_id,ts,kind,data_json) VALUES(?,?,?,?)",
                (jid, now, artifact["kind"], artifact["data_json"]),
            )
        self._event(c, jid, "cache_hit", {"from_job": source_id, "status": "completed"})
        return jid

    def add(self, spec: dict[str, Any]) -> int:
        canonical, provider, deployment, _, _, paths, reserved, role, expected, priority = (
            self._validate_spec(spec)
        )
        now = self._clock()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        fingerprint = self._cache_fingerprint(canonical, deployment, paths, role, expected)
        with self._conn() as c:
            cached = self._explicit_cache_copy(
                c,
                canonical,
                fingerprint,
                provider,
                deployment,
                role,
                expected,
                paths,
                reserved,
                priority,
            )
            if cached is not None:
                return cached
            cur = c.execute(
                "INSERT INTO jobs(created_at,updated_at,created_day,status,priorit"
                "y,provider,deployment,role,expected_deliverable,"
                "allowed_paths_json,reserved_tokens,cache_fingerprint,spec_json) V"
                "ALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    now,
                    now,
                    day,
                    "queued",
                    priority,
                    provider,
                    deployment,
                    role,
                    expected,
                    json.dumps(paths),
                    reserved,
                    fingerprint,
                    json.dumps(canonical, separators=(",", ":"), sort_keys=True),
                ),
            )
            if cur.lastrowid is None:
                raise RuntimeError("missing_job_id")
            jid = cur.lastrowid
            self._event(c, jid, "added", {"status": "queued", "priority": priority})
            return jid

    def revise(self, job_id: int, changes: dict[str, Any]) -> tuple[int, int]:
        if not isinstance(changes, dict):
            raise ValueError("invalid_changes")
        now = self._clock()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        with self._conn() as c:
            row = c.execute("SELECT spec_json FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise ValueError("unknown_job")
            base = json.loads(row["spec_json"])
            revised = dict(base)
            revised.update(changes)
            canonical, provider, deployment, _, _, paths, reserved, role, expected, priority = (
                self._validate_spec(revised)
            )
            fp = self._cache_fingerprint(canonical, deployment, paths, role, expected)
            cur = c.execute(
                "INSERT INTO jobs(parent_id,created_at,updated_at,created_day,stat"
                "us,priority,provider,deployment,role,expected_deliverable,"
                "allowed_paths_json,reserved_tokens,cache_fingerprint,spec_json) V"
                "ALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    now,
                    now,
                    day,
                    "queued",
                    priority,
                    provider,
                    deployment,
                    role,
                    expected,
                    json.dumps(paths),
                    reserved,
                    fp,
                    json.dumps(canonical, separators=(",", ":"), sort_keys=True),
                ),
            )
            if cur.lastrowid is None:
                raise RuntimeError("missing_job_id")
            child = cur.lastrowid
            self._event(c, job_id, "revised", {"child_id": child})
            self._event(c, child, "derived_from", {"parent_id": job_id})
            return child, job_id

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        q = (
            "SELECT id,parent_id,created_at,updated_at,status,priority,attempt"
            "s,cancel_requested,retry_after,provider,deployment "
            "FROM jobs"
        )
        args: tuple[Any, ...] = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        q += " ORDER BY id DESC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def show(self, job_id: int) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return None
            out = dict(row)
            out["spec"] = json.loads(out.pop("spec_json"))
            out["allowed_proposal_paths"] = json.loads(out.pop("allowed_paths_json") or "[]")
            out["events"] = [
                dict(r) | {"data": json.loads(r["data_json"])}
                for r in c.execute(
                    "SELECT ts,type,data_json FROM events WHERE job_id=? ORDER BY id", (job_id,)
                )
            ]
            out["artifacts"] = [
                dict(r) | {"data": json.loads(r["data_json"])}
                for r in c.execute(
                    "SELECT ts,kind,data_json FROM artifacts WHERE job_id=? ORDER BY id", (job_id,)
                )
            ]
            for section in ("events", "artifacts"):
                for item in out[section]:
                    item.pop("data_json", None)
            return out

    def pause(self) -> None:
        with self._conn() as c:
            c.execute("UPDATE controls SET value='1' WHERE key='paused'")

    def resume(self) -> None:
        with self._conn() as c:
            c.execute("UPDATE controls SET value='0' WHERE key='paused'")

    def cancel(self, job_id: int) -> bool:
        now = self._clock()
        with self._conn() as c:
            row = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return False
            status = row["status"]
            if status in TERMINAL_STATUSES:
                return True
            if status == "queued":
                c.execute(
                    "UPDATE jobs SET status='cancelled',cancel_requested=1,updated_at="
                    "? WHERE id=? AND status='queued'",
                    (now, job_id),
                )
                self._event(c, job_id, "cancelled", {"phase": "queued"})
            elif status == "running":
                c.execute(
                    "UPDATE jobs SET cancel_requested=1,updated_at=? WHERE id=?", (now, job_id)
                )
                self._event(c, job_id, "cancel_requested", {"phase": "running"})
            return True

    def claim(self, worker_id: str) -> dict[str, Any] | None:
        if not isinstance(worker_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", worker_id):
            raise ValueError("invalid_worker_id")
        now = self._clock()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            paused = c.execute("SELECT value FROM controls WHERE key='paused'").fetchone()
            if paused and paused[0] == "1":
                return None
            running = c.execute("SELECT id,deployment FROM jobs WHERE status='running'").fetchall()
            if len(running) >= self.max_global:
                return None
            occupied = {r["deployment"] for r in running if r["deployment"]}
            minute_since = now - 60.0
            if (
                self.per_minute_request_limit is not None
                and self._sum_requests(c, since=minute_since) >= self.per_minute_request_limit
            ):
                return None

            rows = c.execute(
                "SELECT * FROM jobs WHERE status='queued' AND cancel_requested=0 A"
                "ND (retry_after IS NULL OR retry_after<=?) "
                "ORDER BY priority DESC,id LIMIT 64",
                (now,),
            ).fetchall()
            for row in rows:
                spec = json.loads(row["spec_json"])
                try:
                    canonical, provider, deployment, _, _, _, reserved, _, _, _ = (
                        self._validate_spec(spec)
                    )
                except ValueError:
                    c.execute(
                        "UPDATE jobs SET status='failed',updated_at=? WHERE id=?", (now, row["id"])
                    )
                    self._event(c, row["id"], "invalid_spec", {"status": "failed"})
                    continue
                if deployment in occupied:
                    continue
                cap = self.provider_daily_claim_cap.get(provider)
                if cap is not None:
                    count = c.execute(
                        "SELECT COUNT(1) FROM dispatch_attempts WHERE dispatch_day=? AND job_id IN "
                        "(SELECT id FROM jobs WHERE provider=?)",
                        (day, provider),
                    ).fetchone()[0]
                    if int(count) >= int(cap):
                        continue
                if self._sum_tokens(c, day=day) + reserved > self.day_token_cap:
                    continue
                if self._sum_tokens(c, run_identity=self.identity) + reserved > self.run_token_cap:
                    continue
                if (
                    self.per_minute_token_limit is not None
                    and self._sum_tokens(c, since=minute_since) + reserved
                    > self.per_minute_token_limit
                ):
                    continue
                claim_token = secrets.token_hex(8)
                updated = c.execute(
                    "UPDATE jobs SET status='running',worker_id=?,owner_pid=?,claim_to"
                    "ken=?,claimed_identity=?,claimed_at=?,updated_at=?,"
                    "attempts=attempts+1,provider=?,deployment=?,reserved_tokens=?,spec_json=? "
                    "WHERE id=? AND status='queued'",
                    (
                        worker_id,
                        int(self._pid_fn()),
                        claim_token,
                        self.identity,
                        now,
                        now,
                        provider,
                        deployment,
                        reserved,
                        json.dumps(canonical, separators=(",", ":"), sort_keys=True),
                        row["id"],
                    ),
                )
                if updated.rowcount != 1:
                    continue
                c.execute(
                    "INSERT INTO dispatch_attempts(job_id,claim_token,run_identity,dis"
                    "patch_day,reserved_estimate,status,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (row["id"], claim_token, self.identity, day, reserved, "running", now),
                )
                self._event(
                    c, row["id"], "claimed", {"worker_id": worker_id, "identity": self.identity}
                )
                return {
                    "id": row["id"],
                    "spec": canonical,
                    "attempts": int(row["attempts"]) + 1,
                    "claim_token": claim_token,
                }
            return None

    def next_ready_delay(self) -> float | None:
        with self._conn() as connection:
            row = connection.execute(
                "SELECT MIN(retry_after) FROM jobs WHERE status='queued' "
                "AND cancel_requested=0 AND retry_after>?",
                (self._clock(),),
            ).fetchone()
        return max(0.0, row[0] - self._clock()) if row and row[0] is not None else None

    def _parse_usage(self, usage: Any) -> dict[str, Any]:
        if usage is None:
            return {}
        if not isinstance(usage, dict):
            raise ValueError("invalid_usage")
        out: dict[str, Any] = {}
        for key, value in usage.items():
            if key in {
                "input_tokens_details",
                "output_tokens_details",
                "prompt_tokens_details",
                "completion_tokens_details",
            }:
                if not isinstance(value, dict) or any(
                    type(number) is not int or number < 0 for number in value.values()
                ):
                    raise ValueError("invalid_usage_details")
                out[key] = {
                    detail: number
                    for detail, number in value.items()
                    if detail
                    in {
                        "cached_tokens",
                        "reasoning_tokens",
                        "audio_tokens",
                        "text_tokens",
                        "image_tokens",
                        "accepted_prediction_tokens",
                        "rejected_prediction_tokens",
                    }
                }
            if key in {
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "input_tokens",
                "output_tokens",
            }:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("invalid_usage")
                out[key] = int(value)
        if "total_tokens" not in out:
            for input_key, output_key in (
                ("prompt_tokens", "completion_tokens"),
                ("input_tokens", "output_tokens"),
            ):
                if input_key in out and output_key in out:
                    out["total_tokens"] = out[input_key] + out[output_key]
                    break
        return out

    def finish(
        self, job_id: int, status: str, artifact: dict[str, Any] | None, meta: dict[str, Any]
    ) -> None:
        now = self._clock()
        with self._conn() as c:
            row = c.execute(
                "SELECT status,cancel_requested,claim_token,reserved_tokens FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if row is None or row["status"] != "running":
                raise ValueError("not_running")
            if status not in TERMINAL_STATUSES:
                raise ValueError("invalid_terminal_status")
            token = meta.get("claim_token")
            if token is None or token != row["claim_token"]:
                raise ValueError("claim_token_mismatch")
            if row["cancel_requested"]:
                status = "cancelled"
                artifact = None
            usage = self._parse_usage(meta.get("usage"))
            actual = usage.get("total_tokens")
            if actual is not None:
                actual = max(
                    actual,
                    usage.get("input_tokens", usage.get("prompt_tokens", 0))
                    + usage.get("output_tokens", usage.get("completion_tokens", 0)),
                )
            if meta.get("accepted") is False:
                actual = 0
            c.execute(
                "UPDATE jobs SET status=?,updated_at=?,actual_tokens=?,claim_token"
                "=NULL WHERE id=? AND status='running'",
                (status, now, actual or 0, job_id),
            )
            c.execute(
                "UPDATE dispatch_attempts SET status=?,actual_tokens=?,finished_at"
                "=? WHERE claim_token=? AND status='running'",
                (status, actual, now, row["claim_token"]),
            )
            if artifact is not None:
                c.execute(
                    "INSERT INTO artifacts(job_id,ts,kind,data_json) VALUES(?,?,?,?)",
                    (
                        job_id,
                        now,
                        "result",
                        json.dumps(artifact, separators=(",", ":"), sort_keys=True),
                    ),
                )
            safe_event = {"status": status, "usage": usage}
            for name in ("model", "reason", "provider_error", "finish"):
                value = meta.get(name)
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
                    safe_event[name] = value
            if type(meta.get("status")) is int:
                safe_event["http_status"] = meta["status"]
            if meta.get("uncertain") is True:
                safe_event["uncertain"] = True
            self._event(
                c, job_id, "finished", {k: v for k, v in safe_event.items() if v is not None}
            )

    def is_cancel_requested(self, job_id: int) -> bool:
        with self._conn() as c:
            r = c.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
            return bool(r and r[0])

    def requeue_with_retry(
        self, job_id: int, retry_after_s: float, reason: str, *, claim_token: str | None = None
    ) -> None:
        if reason not in ALLOWED_REQUEUE_REASONS:
            raise ValueError("retry_reason_not_allowed")
        if (
            isinstance(retry_after_s, bool)
            or not isinstance(retry_after_s, (int, float))
            or not math.isfinite(retry_after_s)
        ):
            raise ValueError("invalid_retry_after")
        if float(retry_after_s) < 0:
            raise ValueError("invalid_retry_after")
        now = self._clock()
        with self._conn() as c:
            row = c.execute(
                "SELECT status,cancel_requested,attempts,claim_token FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown_job")
            if row["status"] != "running":
                raise ValueError("retry_requires_running")
            if claim_token is None or claim_token != row["claim_token"]:
                raise ValueError("claim_token_mismatch")
            if row["cancel_requested"]:
                raise ValueError("retry_disallowed_cancel_requested")
            if int(row["attempts"] or 0) >= self.max_retry_revisions:
                raise ValueError("retry_budget_exhausted")
            c.execute(
                "UPDATE jobs SET status='queued',retry_after=?,updated_at=?,worker"
                "_id=NULL,owner_pid=NULL,claim_token=NULL,"
                "claimed_identity=NULL,claimed_at=NULL WHERE id=? AND status='running'",
                (now + float(retry_after_s), now, job_id),
            )
            c.execute(
                "UPDATE dispatch_attempts SET status='retry',actual_tokens=0,finis"
                "hed_at=? WHERE claim_token=? AND status='running'",
                (now, row["claim_token"]),
            )
            self._event(
                c, job_id, "retry_scheduled", {"after_s": float(retry_after_s), "reason": reason}
            )

    def recover_dead_jobs(self) -> int:
        now = self._clock()
        recovered = 0
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute(
                "SELECT id,owner_pid,claimed_identity,claim_token FROM jobs WHERE status='running'"
            ).fetchall()
            for row in rows:
                pid = int(row["owner_pid"] or 0)
                if pid > 0 and self._is_pid_alive(pid):
                    continue
                changed = c.execute(
                    "UPDATE jobs SET status='uncertain',updated_at=?,claim_token=NULL "
                    "WHERE id=? AND status='running'",
                    (now, row["id"]),
                )
                if changed.rowcount != 1:
                    continue
                c.execute(
                    "UPDATE dispatch_attempts SET status='uncertain',finished_at=? WHE"
                    "RE claim_token=? AND status='running'",
                    (now, row["claim_token"]),
                )
                recovered += 1
                self._event(
                    c,
                    row["id"],
                    "recovered_dead_job",
                    {
                        "owner_pid": pid,
                        "claimed_identity": row["claimed_identity"],
                        "uncertain": True,
                    },
                )
        return recovered
