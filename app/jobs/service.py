"""Defer, read, retry and cancel jobs; a caller sees their own jobs, managers see all."""

from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure import queue
from app.infrastructure.access import PERMISSIONS, Caller
from app.infrastructure.config.settings import QUEUES
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import Conflict, NotFound
from app.jobs import storage


def manager(caller: Caller) -> bool:
    return bool(caller.roles & PERMISSIONS["library.manage"])


def defer(session: Session, kind: str, args: dict, requested_by: Any = None) -> int:
    """Queue one job in the caller's transaction; a held lock key returns the active job."""
    declared = queue.find(kind)
    if declared is None:
        raise NotFound()
    lock = (declared.lock_key(args) or None) if declared.lock_key else None
    if lock is not None and (existing := storage.active(session, lock)) is not None:
        return existing
    values = args | ({"requested_by": str(requested_by)} if requested_by is not None else {})
    return storage.defer(session, declared.queue, kind, values, lock)


def running_count(session: Session, kind: str, requested_by: Any) -> int:
    return storage.running_count(session, kind, str(requested_by))


def active(session: Session, lock: str) -> int | None:
    return storage.active(session, lock)


def state(session: Session, job_id: int) -> str | None:
    row = storage.read(session, job_id)
    return row["state"] if row else None


def cancel_by_id(session: Session, job_id: int) -> bool:
    return storage.cancel(session, job_id)


def shape(row) -> dict[str, Any]:
    result = {
        "job_id": str(row["job_id"]),
        "kind": row["kind"],
        "queue": row["queue"],
        "status": row["state"],
        "attempts": row["attempts"],
        "requested_by": row["requested_by"],
        "error": row["error"],
    }
    for key in ("enqueued_at", "started_at", "finished_at"):
        result[key] = row[key].isoformat() if row[key] else None
    return result


def _owned(session: Session, caller: Caller, job_id: int):
    row = storage.read(session, job_id)
    if row is None or not (manager(caller) or row["requested_by"] == str(caller.user_id)):
        raise NotFound()
    return row


def read(caller: Caller, job_id: int) -> dict[str, Any]:
    assert caller.sessions is not None
    with unit_of_work(caller.sessions, read_only=True) as session:
        return shape(_owned(session, caller, job_id))


def page(caller: Caller, state: str | None, offset: int, limit: int) -> dict[str, Any]:
    assert caller.sessions is not None
    owner = None if manager(caller) else str(caller.user_id)
    with unit_of_work(caller.sessions, read_only=True) as session:
        rows = storage.page(session, owner, state, offset, limit)
    return {
        "jobs": [shape(row) for row in rows[:limit]],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


def retry(caller: Caller, job_id: int) -> dict[str, Any]:
    """A failed, canceled or stopped job runs again as a new job with the same arguments."""
    assert caller.sessions is not None
    with unit_of_work(caller.sessions) as session:
        row = _owned(session, caller, job_id)
        declared = queue.find(row["kind"])
        if declared is None or not declared.retry:
            raise Conflict()
        if row["state"] not in {"failed", "canceled", "stopped"}:
            raise Conflict()
        args = {key: value for key, value in row["args"].items() if key != "requested_by"}
        new = defer(session, row["kind"], args, row["requested_by"])
        return shape(storage.read(session, new))


def cancel(caller: Caller, job_id: int) -> dict[str, Any]:
    assert caller.sessions is not None
    with unit_of_work(caller.sessions) as session:
        _owned(session, caller, job_id)
        if not storage.cancel(session, job_id):
            raise Conflict()
        return shape(storage.read(session, job_id))


def _seconds(value) -> float | None:
    return None if value is None else round(float(value), 3)


def health(sessions, *, since_seconds: float = 3600.0) -> dict[str, Any]:
    """Counts plus last-hour wait/run percentiles and busy workers."""
    from app.infrastructure.queue import STALLED_SECONDS

    with unit_of_work(sessions, read_only=True) as session:
        rows = storage.counts(session)
        percentiles = {row["queue"]: row for row in storage.job_percentiles(session, since_seconds)}
        samples = {row["queue"]: row for row in storage.sample_stats(session, since_seconds)}
        alive = storage.workers_alive(session, STALLED_SECONDS)
    queues = {
        name: {
            "queue": name,
            "oldest_queued_seconds": None,
            "wait_p50_seconds": None,
            "wait_p95_seconds": None,
            "wait_max_seconds": None,
            "run_p50_seconds": None,
            "run_p95_seconds": None,
            "run_max_seconds": None,
            "busy_workers": 0,
            "concurrency": 0,
            "peak_queued": 0,
            "saturated_seconds": 0.0,
        }
        | dict.fromkeys(STATES, 0)
        for name in QUEUES
    }
    for row in rows:
        entry = queues.setdefault(
            row["queue"],
            {"queue": row["queue"], "oldest_queued_seconds": None} | dict.fromkeys(STATES, 0),
        )
        entry[row["state"]] = row["jobs"]
        if row["state"] == "queued" and row["oldest"] is not None:
            entry["oldest_queued_seconds"] = round(float(row["oldest"]), 1)
    for name, entry in queues.items():
        stats = percentiles.get(name)
        if stats is not None:
            entry["wait_p50_seconds"] = _seconds(stats["wait_p50"])
            entry["wait_p95_seconds"] = _seconds(stats["wait_p95"])
            entry["wait_max_seconds"] = _seconds(stats["wait_max"])
            entry["run_p50_seconds"] = _seconds(stats["run_p50"])
            entry["run_p95_seconds"] = _seconds(stats["run_p95"])
            entry["run_max_seconds"] = _seconds(stats["run_max"])
        sample = samples.get(name)
        if sample is not None:
            entry["busy_workers"] = int(sample["workers_alive"] or 0)
            entry["concurrency"] = int(sample["concurrency"] or 0)
            entry["peak_queued"] = int(sample["peak_queued"] or 0)
            entry["saturated_seconds"] = float(sample["saturated_seconds"] or 0)
        elif alive:
            entry["busy_workers"] = int(alive)
    return {"queues": list(queues.values()), "dead": sum(q["failed"] for q in queues.values())}


def report(sessions, *, since_seconds: float = 86400.0) -> dict[str, Any]:
    """Totals for `craft queues report`: per queue and kind, failures, saturation."""
    from app.infrastructure.queue import SAMPLE_SECONDS

    with unit_of_work(sessions, read_only=True) as session:
        kinds = storage.kind_stats(session, since_seconds)
        samples = {
            row["queue"]: row
            for row in storage.sample_stats(session, since_seconds, SAMPLE_SECONDS)
        }
        percentiles = {row["queue"]: row for row in storage.job_percentiles(session, since_seconds)}
    by_queue: dict[str, dict[str, Any]] = {}
    for name in QUEUES:
        sample = samples.get(name, {})
        stats = percentiles.get(name, {})
        by_queue[name] = {
            "queue": name,
            "wait_p50_seconds": _seconds(stats.get("wait_p50")),
            "wait_p95_seconds": _seconds(stats.get("wait_p95")),
            "wait_max_seconds": _seconds(stats.get("wait_max")),
            "run_p50_seconds": _seconds(stats.get("run_p50")),
            "run_p95_seconds": _seconds(stats.get("run_p95")),
            "run_max_seconds": _seconds(stats.get("run_max")),
            "peak_queued": int(sample.get("peak_queued") or 0),
            "saturated_seconds": float(sample.get("saturated_seconds") or 0),
            "workers_alive": int(sample.get("workers_alive") or 0),
            "concurrency": int(sample.get("concurrency") or 0),
            "kinds": {},
            "failures": {},
        }
    for row in kinds:
        entry = by_queue.setdefault(
            row["queue"],
            {"queue": row["queue"], "kinds": {}, "failures": {}},
        )
        kind = entry["kinds"].setdefault(
            row["kind"],
            {
                "kind": row["kind"],
                "jobs": 0,
                "outcomes": {},
                "wait_p50_seconds": None,
                "wait_p95_seconds": None,
                "wait_max_seconds": None,
                "run_p50_seconds": None,
                "run_p95_seconds": None,
                "run_max_seconds": None,
            },
        )
        kind["jobs"] += int(row["jobs"])
        kind["outcomes"][row["outcome"]] = kind["outcomes"].get(row["outcome"], 0) + int(
            row["jobs"]
        )
        # Prefer finished-job percentiles when present on this row.
        for key, column in (
            ("wait_p50_seconds", "wait_p50"),
            ("wait_p95_seconds", "wait_p95"),
            ("wait_max_seconds", "wait_max"),
            ("run_p50_seconds", "run_p50"),
            ("run_p95_seconds", "run_p95"),
            ("run_max_seconds", "run_max"),
        ):
            value = _seconds(row[column])
            if value is not None and kind[key] is None:
                kind[key] = value
        if row["outcome"] == "failed" and row["error"]:
            entry["failures"][row["error"]] = entry["failures"].get(row["error"], 0) + int(
                row["jobs"]
            )
    return {"since_seconds": since_seconds, "queues": list(by_queue.values())}


STATES = ("queued", "scheduled", "started", "stopping", "finished", "failed", "canceled", "stopped")
