"""Background jobs: list, read, retry and stop them."""

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
    """Read one background job's state, attempts and last error class."""
    return Job(**service.read(caller, int(request.job_id)))


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


def cancel_job_by_id(session, job_id: int) -> bool:
    """Cancel or stop a job the calling feature already authorized."""
    return service.cancel_by_id(session, job_id)


def queue_counts(sessions) -> dict:
    """Counts for /health/queue."""
    return service.health(sessions)
