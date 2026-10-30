"""Background jobs on PostgreSQL (Procrastinate): the job registry, retry policy and worker.

Three queues, one worker each: `agent` (chat turns), `interactive` (someone is waiting) and
`bulk` (long batch work), so a long job never holds up a person. Every worker runs the same
image and command, `craft worker`, with CRAFT_WORKER_QUEUES and per-queue concurrency settings.
Job arguments are JSON and hold IDs only: never document text, prompts or tokens.
"""

import asyncio
import contextlib
import inspect
import logging
import signal
import socket
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

import psycopg
from procrastinate import App, BaseRetryStrategy, JobContext, PsycopgConnector, RetryDecision
from procrastinate.exceptions import JobAborted
from procrastinate.job_context import AbortReason
from procrastinate.jobs import Job, Status
from procrastinate.worker import Worker
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure import heartbeat
from app.infrastructure.config.settings import QUEUES, Settings
from app.infrastructure.database.engine import conninfo, make_engine, make_sessions

logger = logging.getLogger("craft.jobs")

# Modules that declare job operations (`@operation(..., job=Job(...))`); the worker imports them.
KIND_MODULES = ("app.agent.jobs", "app.maintenance.jobs", "app.workspace.jobs")
# Waits between automatic retries (transient errors only) and the SIGTERM grace per queue.
WAITS = {"agent": (), "interactive": (5, 30, 120), "bulk": (60, 300, 1800)}
# The agent grace is a run's ceiling, app.agent.storage.RUN_SECONDS.
GRACE = {"agent": 900.0, "interactive": 120.0, "bulk": 120.0}
HEARTBEAT_SECONDS = 10.0
# LISTEN/NOTIFY wakes a worker for new jobs; this poll finds retries whose wait has passed.
POLL_SECONDS = 5.0
# A worker silent this long is dead; its jobs are retried or marked failed.
STALLED_SECONDS = 30.0
RECOVER_SECONDS = 15.0
# How often workers write queue depth samples for craft queues report.
SAMPLE_SECONDS = 30.0
assert set(WAITS) == set(GRACE) == set(QUEUES)


class TransientError(Exception):
    """A failure worth retrying: a timeout, a rate limit, an unavailable service."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("transient")
        self.retry_after = retry_after


class PermanentError(Exception):
    """A failure that a retry cannot fix."""


class WorkerLost(Exception):
    """Recorded for a job whose worker stopped sending heartbeats mid-job."""


def transient(error: BaseException) -> bool:
    """Timeouts, 408, 429, 5xx and connection errors are transient; everything else is not."""
    if isinstance(error, PermanentError):
        return False
    if isinstance(error, (TransientError, TimeoutError, ConnectionError, psycopg.OperationalError)):
        return True
    status = getattr(error, "status_code", None)
    if status is None and type(error).__name__ in {"RateLimited", "Unavailable"}:
        status = getattr(error, "status", None)
    return isinstance(status, int) and (status in (408, 429) or status >= 500)


def retry_after(error: BaseException) -> float | None:
    value = getattr(error, "retry_after", None)
    if value is None:
        headers = getattr(getattr(error, "response", None), "headers", None) or {}
        value = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        return min(float(value), 3600.0) if value is not None else None
    except (TypeError, ValueError):
        return None


class Backoff(BaseRetryStrategy):
    """Retry transient failures after the queue's waits; Retry-After wins when given."""

    def __init__(self, waits: tuple[int, ...]) -> None:
        self.waits = waits

    def get_retry_decision(self, *, exception: BaseException, job: Job) -> RetryDecision | None:
        if job.attempts >= len(self.waits) or not transient(exception):
            return None
        wait = retry_after(exception)
        wait = self.waits[job.attempts] if wait is None else wait
        return RetryDecision(retry_in={"seconds": round(wait)})


@dataclass(frozen=True)
class JobRun:
    """What a job function gets besides its arguments."""

    settings: Settings
    sessions: sessionmaker[Session]
    id: int
    attempt: int
    context: JobContext
    # When the job last wrote its progress (report_progress writes at most once a second).
    progress_at: list[float] = field(default_factory=lambda: [0.0], repr=False, compare=False)

    def should_abort(self) -> bool:
        """Blocking jobs check this between steps; async jobs are cancelled instead."""
        return self.context.should_abort()


@dataclass(frozen=True)
class Kind:
    name: str
    func: Callable[..., Any]
    queue: str
    timeout: float | None
    retry: bool
    lock_key: Callable[[dict], str] | None
    interrupted: Callable[[sessionmaker[Session], dict], None] | None
    every: str | None = None
    every_args: dict | None = None
    notify: bool = False


KINDS: dict[str, Kind] = {}


def register_job(
    kind: str,
    func: Callable[..., Any],
    *,
    queue: str,
    timeout: float | None = None,
    retry: bool = True,
    lock_key: Callable[[dict], str] | None = None,
    interrupted: Callable[[sessionmaker[Session], dict], None] | None = None,
    every: str | None = None,
    every_args: dict | None = None,
    notify: bool = False,
) -> Kind:
    """Register a job kind: `func(job: JobRun, **args)`, sync (run in a thread) or async.
    Features declare jobs as operations instead (`Job` in app.infrastructure.operations).

    A job may run more than once (a retry, or a worker that died after its effect), so its
    effects must be idempotent: keyed by content hash or written as upserts, never "insert
    another row". With retry=False the job is never retried, automatically or by hand (chat
    turns and notebook runs: tools can change things and every attempt costs model money).
    lock_key maps the arguments to a key: a second request while one is queued or running
    returns that job. timeout applies to async jobs. interrupted runs when the job's worker
    died and the job will not be retried. every (cron) queues the job once per tick with the
    arguments every_args; notify tells the requester when the job finishes or finally fails.
    """
    if queue not in QUEUES:
        raise ValueError(f"unknown queue {queue}")
    if kind in KINDS:
        raise ValueError(f"job kind {kind} is already registered")
    KINDS[kind] = Kind(
        kind, func, queue, timeout, retry, lock_key, interrupted, every, every_args, notify
    )
    return KINDS[kind]


def kinds() -> dict[str, Kind]:
    for module in KIND_MODULES:
        import_module(module)
    return KINDS


def find(kind: str) -> Kind | None:
    return kinds().get(kind)


def _record_failure(settings: Settings, job_id: int, attempt: int, error: BaseException) -> None:
    """The error's class only: messages can carry document text or credentials."""
    try:
        with psycopg.connect(conninfo(settings), autocommit=True) as connection:
            connection.execute(
                "INSERT INTO job_attempt_errors (job_id, attempt, error) VALUES (%s, %s, %s)"
                " ON CONFLICT (job_id, attempt) DO UPDATE SET error = excluded.error",
                (job_id, attempt, type(error).__name__[:200]),
            )
    except psycopg.Error:
        logger.warning("job error not recorded", extra={"fields": {"job_id": job_id}})


def _retries(kind: Kind, job: Job, error: BaseException) -> bool:
    """Whether the queue runs the job again after this failure, as Backoff decides."""
    return kind.retry and job.attempts < len(WAITS[kind.queue]) and transient(error)


def _ended(settings: Settings, sessions: sessionmaker[Session], job: Job, *, failed: bool) -> None:
    """Tell the person who queued a notify kind that it finished or finally failed."""
    from uuid import UUID

    from app.infrastructure.access import Caller
    from app.notifications.handlers import JobEnded, job_ended

    requested_by = job.task_kwargs.get("requested_by")
    if not isinstance(requested_by, str) or job.id is None:
        return
    ended = JobEnded(user=UUID(requested_by), job_id=job.id, failed=failed)
    try:
        job_ended(Caller(sessions=sessions, settings=settings), ended)
    except Exception:
        logger.warning("job notification not recorded", extra={"fields": {"job_id": job.id}})


def _log(kind: Kind, job: Job, outcome: str, started: float) -> None:
    fields = {
        "kind": kind.name,
        "job_id": job.id,
        "queue": kind.queue,
        "attempt": job.attempts + 1,
        "outcome": outcome,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    if "timestamp" in job.task_kwargs:
        fields["scheduled"] = True
    logger.info("job", extra={"fields": fields})


def _task(settings: Settings, sessions: sessionmaker[Session], kind: Kind) -> Callable[..., Any]:
    def arguments(context: JobContext, args: dict) -> tuple[JobRun, dict]:
        job = context.job
        assert job.id is not None
        # The periodic deferrer adds the tick's timestamp; the job itself doesn't take it.
        values = {
            key: value for key, value in args.items() if key not in {"requested_by", "timestamp"}
        }
        return JobRun(settings, sessions, job.id, job.attempts + 1, context), values

    if inspect.iscoroutinefunction(kind.func):

        async def run_async(context: JobContext, **args: Any) -> Any:
            started = time.monotonic()
            run, values = arguments(context, args)
            try:
                async with asyncio.timeout(kind.timeout):
                    result = await kind.func(run, **values)
            except asyncio.CancelledError:
                # Stopped by its owner, or by a worker shutting down: only the latter is news.
                if kind.notify and context.abort_reason() == AbortReason.SHUTDOWN:
                    await asyncio.to_thread(_ended, settings, sessions, context.job, failed=True)
                _log(kind, context.job, "stopped", started)
                raise
            except BaseException as error:
                await asyncio.to_thread(_record_failure, settings, run.id, run.attempt, error)
                if kind.notify and not _retries(kind, context.job, error):
                    await asyncio.to_thread(_ended, settings, sessions, context.job, failed=True)
                _log(kind, context.job, "failed", started)
                raise
            if kind.notify:
                await asyncio.to_thread(_ended, settings, sessions, context.job, failed=False)
            _log(kind, context.job, "finished", started)
            return result

        return run_async

    def run_sync(context: JobContext, **args: Any) -> Any:
        started = time.monotonic()
        run, values = arguments(context, args)
        try:
            result = kind.func(run, **values)
        except JobAborted:
            if kind.notify and context.abort_reason() == AbortReason.SHUTDOWN:
                _ended(settings, sessions, context.job, failed=True)
            _log(kind, context.job, "stopped", started)
            raise
        except BaseException as error:
            _record_failure(settings, run.id, run.attempt, error)
            if kind.notify and not _retries(kind, context.job, error):
                _ended(settings, sessions, context.job, failed=True)
            _log(kind, context.job, "failed", started)
            raise
        if kind.notify:
            _ended(settings, sessions, context.job, failed=False)
        _log(kind, context.job, "finished", started)
        return result

    return run_sync


def make_app(settings: Settings, sessions: sessionmaker[Session], *, schedules: bool = True) -> App:
    """A Procrastinate app on the app's own login, with every registered kind.

    With schedules, its workers queue the scheduled kinds: every worker tries each tick, and
    procrastinate_periodic_defers lets one job per kind and tick through. The lock runs one
    at a time, and the queueing lock drops a tick while another is still waiting."""
    app = App(connector=PsycopgConnector(conninfo=conninfo(settings), min_size=1, max_size=4))
    for kind in kinds().values():
        waits = WAITS[kind.queue] if kind.retry else ()
        task = app.task(
            name=kind.name,
            queue=kind.queue,
            pass_context=True,
            retry=Backoff(waits) if waits else False,
        )(_task(settings, sessions, kind))
        if schedules and kind.every:
            app.periodic_registry.register_task(
                task=task,
                cron=kind.every,
                periodic_id="",
                configure_kwargs={
                    "task_kwargs": dict(kind.every_args or {}),
                    "lock": kind.name,
                    "queueing_lock": kind.name,
                },
            )
    return app


async def recover(app: App, settings: Settings, sessions: sessionmaker[Session]) -> int:
    """Retry or fail the jobs of workers that stopped sending heartbeats."""
    count = 0
    for job in await app.job_manager.get_stalled_jobs(seconds_since_heartbeat=STALLED_SECONDS):
        kind = KINDS.get(job.task_name)
        waits = WAITS[kind.queue] if kind and kind.retry else ()
        assert job.id is not None
        retried = job.attempts < len(waits)
        try:
            if retried:
                await app.job_manager.retry_job(job)
            else:
                await app.job_manager.finish_job(job, status=Status.FAILED, delete_job=False)
        except Exception:
            # Another worker recovered it first.
            continue
        if kind and not retried:
            # Only the worker whose finish_job succeeded runs the hooks. One that fails is
            # logged, and the lost worker is still recorded below.
            try:
                if kind.interrupted:
                    await asyncio.to_thread(kind.interrupted, sessions, dict(job.task_kwargs))
                if kind.notify:
                    await asyncio.to_thread(_ended, settings, sessions, job, failed=True)
            except Exception as error:
                fields = {
                    "kind": job.task_name,
                    "job_id": job.id,
                    "error_type": type(error).__name__,
                }
                logger.warning("job recovery hook failed", extra={"fields": fields})
        await asyncio.to_thread(_record_failure, settings, job.id, job.attempts + 1, WorkerLost())
        logger.warning(
            "job recovered",
            extra={"fields": {"kind": job.task_name, "job_id": job.id, "queue": job.queue}},
        )
        count += 1
    return count


def _sample_queues(sessions: sessionmaker[Session], queues: list[str], concurrency: int) -> None:
    """Record queued/running depth and alive workers for later sizing decisions."""
    from app.jobs import storage

    with sessions.begin() as session:
        storage.sample_queues(session, queues, concurrency, STALLED_SECONDS)


async def _step(name: str, step: Awaitable[object]) -> None:
    try:
        await step
    except Exception as error:
        fields = {"name": name, "error_type": type(error).__name__}
        logger.warning("job maintenance failed", extra={"fields": fields})


async def _maintain(
    app: App,
    settings: Settings,
    sessions: sessionmaker[Session],
    queues: list[str],
    concurrency: int,
) -> None:
    next_sample = time.monotonic()
    while True:
        heartbeat.FILE.touch()
        # Each step logs its own failure, so one failing step doesn't skip the others.
        await _step("recover", recover(app, settings, sessions))
        now = time.monotonic()
        if now >= next_sample:
            await _step(
                "sample_queues", asyncio.to_thread(_sample_queues, sessions, queues, concurrency)
            )
            next_sample = now + SAMPLE_SECONDS
        await asyncio.sleep(RECOVER_SECONDS)


async def work(
    settings: Settings,
    queues: list[str],
    concurrency: int,
    *,
    started: Callable[[Worker], None] | None = None,
    install_signal_handlers: bool = True,
    grace: float | None = None,
    schedules: bool = True,
) -> None:
    engine = make_engine(settings)
    sessions = make_sessions(engine)
    app = make_app(settings, sessions, schedules=schedules)
    async with app.open_async():
        worker = Worker(
            app,
            queues=queues,
            name=socket.gethostname(),
            concurrency=concurrency,
            fetch_job_polling_interval=POLL_SECONDS,
            shutdown_graceful_timeout=grace or max(GRACE[queue] for queue in queues),
            install_signal_handlers=install_signal_handlers,
            update_heartbeat_interval=HEARTBEAT_SECONDS,
            stalled_worker_timeout=STALLED_SECONDS,
        )
        maintenance = asyncio.create_task(_maintain(app, settings, sessions, queues, concurrency))
        if started is not None:
            started(worker)
        try:
            await worker.run()
        finally:
            maintenance.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await maintenance
            engine.dispose()


def wait_for_schema(settings: Settings) -> None:
    """Migrations run as their own step; until the database is at this code's newest migration
    (which also makes the queue's tables), wait and stay healthy, as readiness does for the API.
    A container's first process ignores SIGTERM without a handler, so stop on it here."""
    from app.health.handlers import current_migration, migration_head

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    head = migration_head()
    logged = False
    while True:
        heartbeat.FILE.touch()
        try:
            if asyncio.run(current_migration(settings)) == head:
                return
        except psycopg.Error:
            pass
        if not logged:
            logger.warning("waiting for the database and its migrations")
            logged = True
        time.sleep(5)


def run_worker(settings: Settings) -> None:
    """`craft worker`: run until SIGTERM, then finish current jobs within the grace period."""
    wait_for_schema(settings)
    queues = settings.queue_names()
    asyncio.run(work(settings, queues, settings.concurrency_for(queues)))


def check_worker() -> bool:
    """`craft worker --check`: this container's worker wrote a heartbeat in the last minute.

    Container probes run `app/infrastructure/heartbeat.py` by path instead, which skips
    importing the `app` package."""
    return heartbeat.healthy()


@contextmanager
def worker_thread(
    settings: Settings, queues: list[str], concurrency: int = 4, *, schedules: bool = False
) -> Iterator[None]:
    """An in-process worker on its own event loop, for tests that need jobs to run.
    It queues no scheduled jobs unless asked, so tests see only the jobs they queue."""
    ready = threading.Event()
    state: dict[str, Any] = {}

    def started(worker: Worker) -> None:
        state["worker"], state["loop"] = worker, asyncio.get_running_loop()
        ready.set()

    def target() -> None:
        try:
            asyncio.run(
                work(
                    settings,
                    queues,
                    concurrency,
                    started=started,
                    install_signal_handlers=False,
                    grace=5.0,
                    schedules=schedules,
                )
            )
        except BaseException as error:
            state["error"] = error
            ready.set()

    thread = threading.Thread(target=target, name="craft-test-worker", daemon=True)
    thread.start()
    ready.wait(30)
    if "error" in state:
        raise state["error"]
    try:
        yield
    finally:
        state["loop"].call_soon_threadsafe(state["worker"].stop)
        thread.join(30)
