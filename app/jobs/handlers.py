"""Background jobs: list, read, retry and stop them. The plain functions below are for other
features, which authorize the job first."""

from typing import Annotated, Literal

import msgspec

from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape
from app.jobs import service

JobState = Literal[
    "queued", "scheduled", "started", "stopping", "finished", "failed", "canceled", "stopped"
]


class JobRef(Shape):
    job_id: Annotated[str, msgspec.Meta(pattern=r"^[0-9]{1,18}$")]


class JobPageRequest(Shape):
    status: JobState | None = None
    offset: Annotated[int, msgspec.Meta(ge=0, le=100_000)] = 0
    limit: Annotated[int, msgspec.Meta(ge=1, le=100)] = 25


class JobProgress(Shape):
    done: int
    total: int
    note: str | None


class Job(Shape):
    job_id: str
    kind: str
    queue: str
    status: JobState
    attempts: int
    requested_by: str | None
    enqueued_at: str | None
    started_at: str | None
    finished_at: str | None
    error: str | None
    # Only on GET /jobs/{jobId}, once the job has reported progress.
    progress: JobProgress | None = None


class JobPage(Shape):
    jobs: list[Job]
    next_offset: int | None


@operation(SIGNED_IN, get="/jobs")
def list_jobs(caller: Caller, request: JobPageRequest) -> JobPage:
    """Page through your background jobs, newest first; managers see everyone's."""
    data = service.page(caller, request.status, request.offset, request.limit)
    return JobPage(jobs=[Job(**job) for job in data["jobs"]], next_offset=data["next_offset"])


@operation(SIGNED_IN, get="/jobs/{jobId}")
def read_job(caller: Caller, request: JobRef) -> Job:
    """Read one background job's state, attempts, last error class and progress."""
    data = service.read(caller, int(request.job_id))
    progress = data.pop("progress")
    return Job(**data, progress=JobProgress(**progress) if progress else None)


@operation(SIGNED_IN, post="/jobs/{jobId}/retry")
def retry_job(caller: Caller, request: JobRef) -> Job:
    """Run a failed, canceled or stopped job again as a new job."""
    return Job(**service.retry(caller, int(request.job_id)))


@operation(SIGNED_IN, post="/jobs/{jobId}/cancel")
def cancel_job(caller: Caller, request: JobRef) -> Job:
    """Cancel a queued job, or ask a running one to stop (it shows as stopping)."""
    return Job(**service.cancel(caller, int(request.job_id)))


def active_job(session, lock: str) -> int | None:
    """The queued or running job holding a lock key (see Job.lock)."""
    return service.active(session, lock)


def running_jobs(session, kind: str, requested_by) -> int:
    """Queued and running jobs of one kind for one requester; serializes concurrent callers."""
    return service.running_count(session, kind, requested_by)


def job_state(session, job_id: int) -> str | None:
    return service.state(session, job_id)


def requested_job(session, job_id: int, kind: str, requested_by) -> tuple[str, dict] | None:
    """The state and input of one requester's job of one kind; None for any other job."""
    return service.requested(session, job_id, kind, requested_by)


def cancel_job_by_id(session, job_id: int) -> bool:
    """Cancel or stop a job the calling feature already authorized."""
    return service.cancel_by_id(session, job_id)


def cancel_queued_job(session, job_id: int) -> bool:
    """Cancel a job the calling feature authorized, only while it is still queued."""
    return service.cancel_queued(session, job_id)


def save_progress(sessions, job_id: int, done: int, total: int, note: str | None) -> None:
    """Record a running job's progress (see operations.jobs.report_progress)."""
    service.save_progress(sessions, job_id, done, total, note)


def prune_progress(session, days: int) -> None:
    """Delete progress not updated for this many days."""
    service.prune_progress(session, days)


def queue_counts(sessions) -> dict:
    """Counts and last-hour percentiles for /health/queue."""
    return service.health(sessions)


def queue_report(sessions, since_seconds: float) -> dict:
    """Totals for `craft queues report`."""
    return service.report(sessions, since_seconds=since_seconds)
