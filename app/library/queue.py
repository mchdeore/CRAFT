"""Durable library-index jobs via Redis-backed RQ."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, cast

from redis import Redis
from redis.exceptions import RedisError, WatchError
from rq import Queue, Worker
from rq.exceptions import InvalidJobOperation, NoSuchJobError
from rq.job import Job

from app.infrastructure.access import Caller, require
from app.infrastructure.config.settings import Settings
from app.infrastructure.errors import ApiError, Conflict, NotFound

JobState = Literal[
    "queued",
    "started",
    "deferred",
    "scheduled",
    "finished",
    "failed",
    "stopped",
    "canceled",
    "unknown",
]
KNOWN_STATES: set[str] = {
    "queued",
    "started",
    "deferred",
    "scheduled",
    "finished",
    "failed",
    "stopped",
    "canceled",
}
REGISTRY_STATES = {
    "canceled": "canceled_job_registry",
    "started": "started_job_registry",
    "deferred": "deferred_job_registry",
    "scheduled": "scheduled_job_registry",
    "failed": "failed_job_registry",
    "finished": "finished_job_registry",
    "stopped": "failed_job_registry",
}
MAX_JOB_OFFSET = 100_000


class QueueUnavailable(ApiError):
    status, code, message = 503, "queue_unavailable", "Queue unavailable."


def index_build(settings: Settings, rebuild: bool, *, enrich_ocr: bool = False) -> dict[str, int]:
    from app.library.index import build

    return build(settings, rebuild, enrich_ocr=enrich_ocr)


class QueueInspector(Worker):
    def _set_connection(self, connection: Redis) -> Redis:
        # Metadata reads must not inherit RQ's long blocking-dequeue timeout.
        return connection


def open_connection(settings: Settings) -> Redis:
    return Redis.from_url(settings.queue_url, socket_connect_timeout=5, socket_timeout=5)


def open_queue(settings: Settings, connection: Redis | None = None) -> Queue:
    return Queue(settings.queue_name, connection=connection or open_connection(settings))


def timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def status_of(job: Job) -> JobState:
    status = job.get_status(refresh=True)
    state = getattr(status, "value", str(status))
    if state == "queued":
        queue = Queue(job.origin, connection=job.connection)
        for candidate, attribute in REGISTRY_STATES.items():
            registry = getattr(queue, attribute)
            if candidate == "started":
                # RQ 2.6 stores composite job:execution members. Only inspect
                # this job's execution index, never the whole started registry.
                executions = cast(
                    list[bytes], job.connection.zrange(job.execution_registry.key, 0, 32)
                )
                if len(executions) > 32:
                    raise QueueUnavailable()
                members = [f"{job.id}:{value.decode()}" for value in executions]
                scores = (
                    cast(list[float | None], job.connection.zmscore(registry.key, members))
                    if members
                    else []
                )
                found = any(score is not None for score in scores)
            else:
                found = job.connection.zscore(registry.key, job.id) is not None
            if found:
                return cast(JobState, candidate)
    return cast(JobState, state) if state in KNOWN_STATES else "unknown"


def index_job_payload(job: Job) -> dict[str, Any]:
    details = job.kwargs if isinstance(job.kwargs, dict) else {}
    latest = job.latest_result(timeout=0)
    result = latest.return_value if latest and isinstance(latest.return_value, dict) else None
    exc = latest.exc_string if latest and isinstance(latest.exc_string, str) else None
    return {
        "job_id": job.id,
        "kind": "ocr" if job.func_name == "app.library.enrichment.run_ocr_job" else "index",
        "item": details.get("item"),
        "version": details.get("version"),
        "force": bool(details.get("force", False)),
        "status": status_of(job),
        "queue": job.origin,
        "rebuild": bool(details.get("rebuild", False)),
        "enrich_ocr": bool(details.get("enrich_ocr", False)),
        "requested_by": details.get("requested_by"),
        "retry_of": details.get("retry_of"),
        "enqueued_at": timestamp(job.enqueued_at),
        "started_at": timestamp(job.started_at),
        "ended_at": timestamp(job.ended_at),
        "result": result,
        "error": exc.rstrip().rsplit("\n", 1)[-1] if exc else None,
    }


def queue_settings(caller: Caller) -> Settings:
    require(caller, "library.manage")
    if caller.settings is None:
        raise NotFound()
    return caller.settings


def load_job(job_id: str, connection: Redis, queue_name: str) -> Job:
    try:
        job = Job.fetch(job_id, connection=connection)
    except NoSuchJobError:
        raise NotFound() from None
    if job.origin != queue_name or job.func_name not in {
        "app.library.queue.run_index_job",
        "app.library.enrichment.run_ocr_job",
    }:
        raise NotFound()
    return job


def enqueue_index_job(caller: Caller, rebuild: bool, *, enrich_ocr: bool = False) -> dict[str, Any]:
    settings = queue_settings(caller)
    connection = open_connection(settings)
    try:
        queue = open_queue(settings, connection)
        job = queue.enqueue(
            run_index_job,
            kwargs={
                "rebuild": rebuild,
                "enrich_ocr": enrich_ocr,
                "requested_by": str(caller.user_id) if caller.user_id else None,
                "retry_of": None,
            },
            job_timeout=60 * 60,
            result_ttl=7 * 24 * 60 * 60,
            failure_ttl=7 * 24 * 60 * 60,
        )
        return index_job_payload(job)
    except RedisError:
        raise QueueUnavailable() from None
    finally:
        connection.close()


def read_index_job(caller: Caller, job_id: str) -> dict[str, Any]:
    settings = queue_settings(caller)
    connection = open_connection(settings)
    try:
        job = load_job(job_id, connection, settings.queue_name)
        return index_job_payload(job)
    except RedisError:
        raise QueueUnavailable() from None
    finally:
        connection.close()


def list_index_jobs(
    caller: Caller, status: JobState = "queued", offset: int = 0, limit: int = 25
) -> dict[str, Any]:
    settings = queue_settings(caller)
    if status not in KNOWN_STATES or not 0 <= offset <= MAX_JOB_OFFSET or not 1 <= limit <= 100:
        raise Conflict("Invalid job page.")
    connection = open_connection(settings)
    try:
        managed = open_queue(settings, connection)
        if status == "queued":
            raw = managed.get_job_ids(offset=offset, length=limit + 1)
        else:
            registry = getattr(managed, REGISTRY_STATES[status])
            raw = registry.get_job_ids(start=offset, end=offset + limit, cleanup=False)
        jobs = []
        seen = set()
        for job_id in raw[:limit]:
            if job_id in seen:
                continue
            seen.add(job_id)
            try:
                payload = index_job_payload(load_job(job_id, connection, settings.queue_name))
            except NotFound:
                continue
            if payload["status"] == status:
                jobs.append(payload)
        next_offset = offset + limit
        return {
            "jobs": jobs,
            "next_offset": next_offset
            if len(raw) > limit and next_offset <= MAX_JOB_OFFSET
            else None,
        }
    except RedisError:
        raise QueueUnavailable() from None
    finally:
        connection.close()


def retry_index_job(caller: Caller, job_id: str) -> dict[str, Any]:
    settings = queue_settings(caller)
    connection = open_connection(settings)
    try:
        queue = open_queue(settings, connection)
        job = load_job(job_id, connection, settings.queue_name)
        state = status_of(job)
        if state not in {"failed", "canceled", "stopped"}:
            raise Conflict("Only failed, canceled or stopped jobs can be retried.")
        function: Callable[..., dict[str, Any]]
        details: dict[str, Any]
        if job.func_name == "app.library.enrichment.run_ocr_job":
            from app.library.enrichment import enqueue_version_job

            retried, _ = enqueue_version_job(
                queue,
                settings,
                job.kwargs["item"],
                job.kwargs["version"],
                force=bool(job.kwargs.get("force", False)),
                requested_by=str(caller.user_id),
                retry_of=job.id,
            )
            assert retried is not None
            return index_job_payload(retried)
        else:
            function = run_index_job
            details = {
                "rebuild": bool(job.kwargs.get("rebuild", False)),
                "enrich_ocr": bool(job.kwargs.get("enrich_ocr", False)),
            }
            timeout = 60 * 60
        details.update(requested_by=str(caller.user_id), retry_of=job.id)
        retried = queue.enqueue(
            function,
            kwargs=details,
            job_timeout=timeout,
            result_ttl=7 * 24 * 60 * 60,
            failure_ttl=7 * 24 * 60 * 60,
        )
        return index_job_payload(retried)
    except RedisError:
        raise QueueUnavailable() from None
    finally:
        connection.close()


def cancel_index_job(caller: Caller, job_id: str) -> dict[str, Any]:
    settings = queue_settings(caller)
    connection = open_connection(settings)
    try:
        job = load_job(job_id, connection, settings.queue_name)
        managed = open_queue(settings, connection)
        with connection.pipeline() as pipeline:
            for _ in range(8):
                try:
                    # A dequeue changes the list before RQ marks the job started.
                    pipeline.watch(job.key, managed.key)
                    job.refresh()
                    state = status_of(job)
                    if state in {"finished", "failed", "canceled", "stopped"}:
                        return index_job_payload(job)
                    if state != "queued" or connection.lpos(managed.key, job.id) is None:
                        raise Conflict("Only jobs still waiting in the queue can be canceled.")
                    pipeline.multi()
                    job.cancel(pipeline=pipeline)
                    pipeline.execute()
                    job.refresh()
                    if job.func_name == "app.library.enrichment.run_ocr_job":
                        from app.library.enrichment import expire_terminal_claim

                        expire_terminal_claim(job, connection)
                    return index_job_payload(job)
                except (WatchError, InvalidJobOperation):
                    pipeline.reset()
            raise Conflict("Queue state kept changing; retry cancellation.")
    except RedisError:
        raise QueueUnavailable() from None
    finally:
        connection.close()


def _registry_count(queue: Queue, attribute: str) -> int:
    registry = getattr(queue, attribute, None)
    return registry.get_job_count(cleanup=False) if registry is not None else 0


def queue_health(caller: Caller) -> dict[str, Any]:
    settings = queue_settings(caller)
    connection = open_connection(settings)
    try:
        queue = open_queue(settings, connection)
        workers = []
        for worker in QueueInspector.all(connection=connection):
            names = [registered.name for registered in worker.queues]
            if settings.queue_name not in names:
                continue
            workers.append(
                {
                    "name": worker.name,
                    "state": worker.get_state(),
                    "queues": names,
                    "successful_jobs": int(getattr(worker, "successful_job_count", 0) or 0),
                    "failed_jobs": int(getattr(worker, "failed_job_count", 0) or 0),
                    "last_heartbeat": timestamp(getattr(worker, "last_heartbeat", None)),
                }
            )
        return {
            "queue": settings.queue_name,
            "queued": queue.count,
            "started": _registry_count(queue, "started_job_registry"),
            "deferred": _registry_count(queue, "deferred_job_registry"),
            "scheduled": _registry_count(queue, "scheduled_job_registry"),
            "failed": _registry_count(queue, "failed_job_registry"),
            "finished": _registry_count(queue, "finished_job_registry"),
            "canceled": _registry_count(queue, "canceled_job_registry"),
            "workers": workers,
        }
    except RedisError:
        raise QueueUnavailable() from None
    finally:
        connection.close()


def run_index_job(
    *,
    rebuild: bool = False,
    requested_by: str | None = None,
    retry_of: str | None = None,
    enrich_ocr: bool = False,
) -> dict[str, int]:
    _ = (requested_by, retry_of)
    return index_build(Settings.from_env(), rebuild, enrich_ocr=enrich_ocr)


def run_worker(settings: Settings, *, burst: bool = False) -> None:
    connection = open_connection(settings)
    try:
        queue = open_queue(settings, connection)
        worker = Worker([queue], connection=connection)
        worker.work(burst=burst, with_scheduler=False)
    except RedisError:
        raise QueueUnavailable() from None
    finally:
        connection.close()
