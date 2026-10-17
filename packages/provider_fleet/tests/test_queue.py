from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import pytest

from craft_fleet.queue import JobQueue


def checked_job(queue: JobQueue, job: int) -> dict[str, Any]:
    value = queue.show(job)
    assert value is not None
    return value


def spec(provider: str = "deepseek", **extra):
    base = {
        "provider": provider,
        "topic": "topic",
        "source_excerpts": [{"source_id": "s1", "text": "value is 12 V"}],
        "max_output_tokens": 256,
        "deadline": 30,
    }
    base.update(extra)
    return base


def test_migration_from_old_schema_preserves_rows_and_refs(tmp_path):
    db = tmp_path / "jobs.sqlite"
    c = sqlite3.connect(db)
    c.executescript(
        """
        CREATE TABLE jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          parent_id INTEGER,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          status TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          cancel_requested INTEGER NOT NULL DEFAULT 0,
          retry_after REAL,
          spec_json TEXT NOT NULL
        );
        CREATE TABLE events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER NOT NULL,
          ts REAL NOT NULL,
          type TEXT NOT NULL,
          data_json TEXT NOT NULL
        );
        CREATE TABLE artifacts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER NOT NULL,
          ts REAL NOT NULL,
          kind TEXT NOT NULL,
          data_json TEXT NOT NULL
        );
        """
    )
    now = time.time()
    parent_spec = json.dumps(spec(topic="old-parent"))
    child_spec = json.dumps(spec(topic="old-child"))
    c.execute(
        "INSERT INTO jobs(created_at,updated_at,status,spec_json,attempts) VALUES(?,?,?,?,1)",
        (now, now, "completed", parent_spec),
    )
    parent = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.execute(
        "INSERT INTO jobs(parent_id,created_at,updated_at,status,spec_json) VALUES(?,?,?,?,?)",
        (parent, now, now, "queued", child_spec),
    )
    child = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.commit()
    c.close()

    q = JobQueue(db, day_token_cap=900)
    parent_row = checked_job(q, parent)
    child_row = checked_job(q, child)
    assert parent_row and child_row
    assert child_row["parent_id"] == parent
    assert child_row["spec"]["topic"] == "old-child"
    assert q.claim("worker") is None
    JobQueue(db)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dispatch_attempts").fetchone()[0] == 1


def test_provider_mapping_unknown_rejected_and_limit_validation(tmp_path):
    with pytest.raises(ValueError):
        JobQueue(tmp_path / "x.sqlite", max_global=3)

    q = JobQueue(tmp_path / "jobs.sqlite", provider_deployments={"known": "dep-a"})
    with pytest.raises(ValueError):
        q.add(spec(provider="unknown"))


def test_requeue_with_retry_is_same_job_running_only(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite", max_retry_revisions=2)
    jid = q.add(spec())
    claim = q.claim("w")
    assert claim and claim["id"] == jid

    q.requeue_with_retry(jid, 0.01, "not_accepted", claim_token=claim["claim_token"])
    row = checked_job(q, jid)
    assert row and row["status"] == "queued"
    assert len(q.list()) == 1

    with pytest.raises(ValueError):
        q.requeue_with_retry(jid, float("inf"), "not_accepted")
    with pytest.raises(ValueError):
        q.requeue_with_retry(jid, 0, "bad")


def test_stale_attempt_cannot_finish_or_requeue_a_new_claim(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite")
    jid = q.add(spec())
    first = q.claim("first")
    assert first is not None
    q.requeue_with_retry(jid, 0, "not_accepted", claim_token=first["claim_token"])
    second = q.claim("second")
    assert second is not None
    for stale in (None, first["claim_token"]):
        with pytest.raises(ValueError, match="claim_token_mismatch"):
            q.finish(jid, "completed", None, {"claim_token": stale})
        with pytest.raises(ValueError, match="claim_token_mismatch"):
            q.requeue_with_retry(jid, 0, "not_accepted", claim_token=stale)
    assert checked_job(q, jid)["claim_token"] == second["claim_token"]


@pytest.mark.parametrize(
    "usage", [{}, {"input_tokens": 1}, {"output_tokens_details": {"reasoning_tokens": 1}}]
)
def test_partial_usage_does_not_release_unknown_charge(tmp_path, usage):
    q = JobQueue(tmp_path / "jobs.sqlite", day_token_cap=900)
    jid = q.add(spec())
    q.add(spec(topic="next"))
    claim = q.claim("first")
    assert claim is not None
    q.finish(jid, "completed", None, {"usage": usage, "claim_token": claim["claim_token"]})
    assert q.claim("second") is None


def test_usage_detail_keys_are_allowlisted_and_inconsistent_total_is_conservative(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite", day_token_cap=900)
    jid = q.add(spec())
    q.add(spec(topic="next"))
    claim = q.claim("first")
    assert claim is not None
    q.finish(
        jid,
        "completed",
        None,
        {
            "claim_token": claim["claim_token"],
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 1000,
                "total_tokens": 1,
                "input_tokens_details": {"cached_tokens": 10, "private source phrase": 4},
            },
        },
    )
    row = checked_job(q, jid)
    assert row["actual_tokens"] == 2000
    assert row["events"][-1]["data"]["usage"]["input_tokens_details"] == {"cached_tokens": 10}
    assert q.claim("second") is None


def test_revise_creates_new_job_and_keeps_original_spec_immutable(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite")
    original = q.add(spec(topic="before", max_output_tokens=300))
    new_id, parent_id = q.revise(original, {"topic": "after"})
    assert parent_id == original
    assert new_id != original

    old = checked_job(q, original)
    new = checked_job(q, new_id)
    assert old and new
    assert old["spec"]["topic"] == "before"
    assert new["spec"]["topic"] == "after"
    assert new["parent_id"] == original


def test_run_and_day_token_caps_include_pending_and_finished(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite", run_token_cap=900, day_token_cap=900)
    a = q.add(spec(topic="a"))
    b = q.add(spec(topic="b"))

    c1 = q.claim("w1")
    assert c1 and c1["id"] == a
    assert q.claim("w2") is None

    q.finish(
        a,
        "completed",
        {"ok": True},
        {"usage": {"total_tokens": 10}, "claim_token": c1["claim_token"]},
    )
    c2 = q.claim("w2")
    assert c2 is not None
    assert c2 and c2["id"] == b


def test_unknown_outcome_keeps_reserved_charge(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite", day_token_cap=900)
    j1 = q.add(spec(topic="x"))
    j2 = q.add(spec(topic="y"))

    c1 = q.claim("w")
    assert c1 is not None
    assert c1 and c1["id"] == j1
    q.finish(
        j1, "uncertain", None, {"usage": {"total_tokens": 1}, "claim_token": c1["claim_token"]}
    )
    assert q.claim("w2") is None
    assert checked_job(q, j2)["status"] == "queued"


def test_finish_claim_token_and_actual_usage_not_clamped(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite")
    jid = q.add(spec())
    claim = q.claim("w")
    assert claim

    with pytest.raises(ValueError):
        q.finish(jid, "completed", None, {"claim_token": "wrong", "usage": {"total_tokens": 7}})

    q.finish(
        jid,
        "completed",
        None,
        {"claim_token": claim["claim_token"], "usage": {"total_tokens": 500000}},
    )
    assert checked_job(q, jid)["actual_tokens"] == 500000

    with pytest.raises(ValueError):
        q.finish(jid, "failed", None, {"usage": {"total_tokens": 1}})


def test_cache_fingerprint_includes_deliverable_paths_and_scans_all_matches(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite", enable_explicit_cache=True)
    j1 = q.add(spec(recipe="r1", expected_deliverable="json", allowed_proposal_paths=["a/b.md"]))
    c1 = q.claim("w")
    assert c1 is not None
    q.finish(
        j1,
        "completed",
        {"summary": "first"},
        {"usage": {"total_tokens": 5}, "claim_token": c1["claim_token"]},
    )

    j2 = q.add(spec(recipe="r2", expected_deliverable="json", allowed_proposal_paths=["a/b.md"]))
    c2 = q.claim("w2")
    assert c2 is not None
    q.finish(
        j2,
        "completed",
        {"summary": "second"},
        {"usage": {"total_tokens": 5}, "claim_token": c2["claim_token"]},
    )

    hit = q.add(
        spec(
            recipe="r1",
            expected_deliverable="json",
            allowed_proposal_paths=["a/b.md"],
            cache_lookup=True,
        )
    )
    assert checked_job(q, hit)["status"] == "completed"
    assert checked_job(q, hit)["artifacts"][0]["data"] == {"summary": "first"}

    miss = q.add(
        spec(
            recipe="r1",
            expected_deliverable="coding_proposal",
            allowed_proposal_paths=["a/b.md"],
            cache_lookup=True,
        )
    )
    assert checked_job(q, miss)["status"] == "queued"


def test_per_minute_limits_and_safe_event_metadata(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite", per_minute_request_limit=1, per_minute_token_limit=900)
    j1 = q.add(spec(topic="one"))
    j2 = q.add(spec(topic="two"))

    c1 = q.claim("w")
    assert c1 is not None
    assert c1 and c1["id"] == j1
    assert q.claim("w2") is None

    q.finish(
        j1,
        "failed",
        None,
        {
            "claim_token": c1["claim_token"],
            "usage": {"prompt_tokens": 3, "completion_tokens": 7},
            "prompt": "secret prompt",
            "api_key": "secret",
        },
    )
    shown = checked_job(q, j1)
    blob = json.dumps(shown["events"])
    assert "secret prompt" not in blob
    assert "api_key" not in blob
    assert "prompt_tokens" in blob
    assert checked_job(q, j2)["status"] == "queued"


def test_legacy_running_deployment_is_reserved_after_migration(tmp_path):
    SCHEMA = """
    CREATE TABLE controls(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
      updated_at REAL NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
      cancel_requested INTEGER NOT NULL DEFAULT 0,worker_id TEXT,claimed_at REAL,
      retry_after REAL,spec_json TEXT NOT NULL);
    CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER NOT NULL,
      ts REAL NOT NULL,type TEXT NOT NULL,data_json TEXT NOT NULL);
    CREATE TABLE artifacts(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER NOT NULL,
      ts REAL NOT NULL,kind TEXT NOT NULL,data_json TEXT NOT NULL);
    """
    db = tmp_path / "old.sqlite"
    with sqlite3.connect(db) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO jobs(created_at,updated_at,status,spec_json) VALUES(0,0,'running',?)",
            (json.dumps(spec("a")),),
        )
    queue = JobQueue(db, provider_deployments={"a": "same", "b": "same", "c": "different"})
    queue.add(spec("b"))
    free = queue.add(spec("c"))
    claimed = queue.claim("worker")
    assert claimed and claimed["id"] == free


def test_missing_usage_keeps_a_conservative_reservation(tmp_path):
    queue = JobQueue(tmp_path / "unknown.sqlite", day_token_cap=900)
    first = queue.add(spec("a"))
    queue.add(spec("b"))
    claim = queue.claim("worker")
    assert claim is not None
    queue.finish(
        first, "failed", None, {"reason": "transport", "claim_token": claim["claim_token"]}
    )
    assert queue.claim("other") is None


def test_reported_cache_and_reasoning_tokens_are_preserved(tmp_path):
    queue = JobQueue(tmp_path / "details.sqlite")
    job = queue.add(spec())
    claim = queue.claim("worker")
    assert claim is not None
    usage = {
        "input_tokens": 3,
        "output_tokens": 5,
        "total_tokens": 8,
        "input_tokens_details": {"cached_tokens": 2},
        "output_tokens_details": {"reasoning_tokens": 1},
    }
    queue.finish(job, "completed", None, {"usage": usage, "claim_token": claim["claim_token"]})
    event = checked_job(queue, job)["events"][-1]["data"]
    assert event["usage"] == usage


def test_safe_event_text_and_symlink_store_guards(tmp_path):
    target = tmp_path / "actual.sqlite"
    queue = JobQueue(target)
    linked = tmp_path / "link.sqlite"
    linked.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        JobQueue(linked)
    job = queue.add(spec())
    claim = queue.claim("worker")
    assert claim is not None
    queue.finish(
        job,
        "failed",
        None,
        {
            "reason": "payload with private words",
            "model": {"secret": "payload"},
            "claim_token": claim["claim_token"],
        },
    )
    report = json.dumps(checked_job(queue, job)["events"])
    assert "private words" not in report and "secret" not in report


def test_rate_and_run_caps_do_not_reset_at_utc_midnight(tmp_path):
    midnight = 1792281600.0
    clock = [midnight - 1]
    queue = JobQueue(
        tmp_path / "midnight.sqlite",
        clock=lambda: clock[0],
        run_token_cap=900,
        per_minute_token_limit=900,
    )
    first = queue.add(spec("a"))
    queue.add(spec("b"))
    claim = queue.claim("first")
    assert claim is not None
    queue.finish(
        first,
        "completed",
        None,
        {"claim_token": claim["claim_token"], "usage": {"total_tokens": 800}},
    )
    clock[0] += 2
    assert queue.claim("second") is None
