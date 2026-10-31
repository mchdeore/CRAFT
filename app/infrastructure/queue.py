"""Background jobs on PostgreSQL (Procrastinate): the job registry and worker.

Three queues, one worker each: `agent` (chat turns), `interactive` (someone is waiting) and
`bulk` (long batch work), so a long job never holds up a person. Every worker runs the same
image and command, `craft worker`, with CRAFT_WORKER_QUEUES and per-queue concurrency settings.
Job arguments are JSON and hold IDs only: never document text, prompts or tokens. A job is
never retried: one whose worker dies is marked failed.
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
from importlib import import_module
from typing import Any

import psycopg
from procrastinate import App, JobContext, PsycopgConnector
from procrastinate.exceptions import JobAborted
from procrastinate.job_context import AbortReason
from procrastinate.jobs import Job, Status
from procrastinate.worker import Worker
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure import heartbeat
from app.infrastructure.config.settings import QUEUES, Settings
from app.infrastructure.database.engine import conninfo, make_engine, make_sessions
from app.infrastructure.operations.jobs import SCHEDULED, RunningJob, interrupt, runner
from app.infrastructure.operations.registry import Operation

logger = logging.getLogger("craft.jobs")

# Modules that declare job operations (`@operation(..., job=Job(...))`); the worker imports them.
JOB_MODULES = ("app.agent.jobs", "app.maintenance.jobs", "app.workspace.jobs")
# The SIGTERM grace per queue; the agent grace is a run's ceiling, app.agent.storage.RUN_SECONDS.
GRACE = {"agent": 900.0, "interactive": 120.0, "bulk": 120.0}
HEARTBEAT_SECONDS = 10.0
# LISTEN/NOTIFY wakes a worker for new jobs; this poll finds one whose notice was missed.
POLL_SECONDS = 5.0
# A worker silent this long is dead; its jobs are marked failed.
STALLED_SECONDS = 30.0
RECOVER_SECONDS = 15.0
# How often workers write queue depth samples for craft queues report.
SAMPLE_SECONDS = 30.0
assert set(GRACE) == set(QUEUES)


class WorkerLost(Exception):
    """Recorded for a job whose worker stopped sending heartbeats mid-job."""


JOBS: dict[str, Operation] = {}


def register_job(operation: Operation) -> None:
    """Register a job operation; app.infrastructure.operations.Job says how it runs.

    A job may still run twice (a worker that died after its effect, then a new request), so its
    effects must be idempotent: keyed by content hash or written as upserts, never "insert
    another row"."""
    assert operation.job is not None
    if operation.job.queue not in QUEUES:
        raise ValueError(f"unknown queue {operation.job.queue}")
    if operation.name in JOBS:
        raise ValueError(f"job {operation.name} is already registered")
    JOBS[operation.name] = operation


def job_operations() -> dict[str, Operation]:
    for module in JOB_MODULES:
        import_module(module)
    return JOBS


def find(name: str) -> Operation | None:
    return job_operations().get(name)


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


def _ended(settings: Settings, sessions: sessionmaker[Session], job: Job, *, failed: bool) -> None:
    """Tell the person who queued a notify job that it finished or failed."""
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


def _log(operation: Operation, job: Job, outcome: str, started: float) -> None:
    assert operation.job is not None
    fields = {
        "kind": operation.name,
        "job_id": job.id,
        "queue": operation.job.queue,
        "attempt": job.attempts + 1,
        "outcome": outcome,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    if "timestamp" in job.task_kwargs:
        fields["scheduled"] = True
    logger.info("job", extra={"fields": fields})


def _task(
    settings: Settings, sessions: sessionmaker[Session], operation: Operation
) -> Callable[..., Any]:
    declared, func = operation.job, runner(operation)
    assert declared is not None

    def arguments(context: JobContext, args: dict) -> tuple[RunningJob, dict]:
        job = context.job
        assert job.id is not None
        # The periodic deferrer adds the tick's timestamp; the job itself doesn't take it.
        values = {
            key: value for key, value in args.items() if key not in {"requested_by", "timestamp"}
        }
        return RunningJob(settings, sessions, job.id, job.attempts + 1, context), values

    if inspect.iscoroutinefunction(func):

        async def run_async(context: JobContext, **args: Any) -> Any:
            started = time.monotonic()
            run, values = arguments(context, args)
            try:
                async with asyncio.timeout(declared.timeout):
                    result = await func(run, **values)
            except asyncio.CancelledError:
                # Stopped by its owner, or by a worker shutting down: only the latter is news.
                if declared.notify and context.abort_reason() == AbortReason.SHUTDOWN:
                    await asyncio.to_thread(_ended, settings, sessions, context.job, failed=True)
                _log(operation, context.job, "stopped", started)
                raise
            except BaseException as error:
                await asyncio.to_thread(_record_failure, settings, run.id, run.attempt, error)
                if declared.notify:
                    await asyncio.to_thread(_ended, settings, sessions, context.job, failed=True)
                _log(operation, context.job, "failed", started)
                raise
            if declared.notify:
                await asyncio.to_thread(_ended, settings, sessions, context.job, failed=False)
            _log(operation, context.job, "finished", started)
            return result

        return run_async

    def run_sync(context: JobContext, **args: Any) -> Any:
        started = time.monotonic()
        run, values = arguments(context, args)
        try:
            result = func(run, **values)
        except JobAborted:
            if declared.notify and context.abort_reason() == AbortReason.SHUTDOWN:
                _ended(settings, sessions, context.job, failed=True)
            _log(operation, context.job, "stopped", started)
            raise
        except BaseException as error:
            _record_failure(settings, run.id, run.attempt, error)
            if declared.notify:
                _ended(settings, sessions, context.job, failed=True)
            _log(operation, context.job, "failed", started)
            raise
        if declared.notify:
            _ended(settings, sessions, context.job, failed=False)
        _log(operation, context.job, "finished", started)
        return result

    return run_sync


def make_app(settings: Settings, sessions: sessionmaker[Session], *, schedules: bool = True) -> App:
    """A Procrastinate app on the app's own login, with every registered job.

    With schedules, its workers queue the scheduled jobs: every worker tries each tick, and
    procrastinate_periodic_defers lets one job per operation and tick through. The lock runs one
    at a time, and the queueing lock drops a tick while another is still waiting."""
    app = App(connector=PsycopgConnector(conninfo=conninfo(settings), min_size=1, max_size=4))
    for operation in job_operations().values():
        assert operation.job is not None
        task = app.task(name=operation.name, queue=operation.job.queue, pass_context=True)(
            _task(settings, sessions, operation)
        )
        if schedules and operation.job.every:
            app.periodic_registry.register_task(
                task=task,
                cron=operation.job.every,
                periodic_id="",
                configure_kwargs={
                    "task_kwargs": dict(SCHEDULED),
                    "lock": operation.name,
                    "queueing_lock": operation.name,
                },
            )
    return app


async def recover(app: App, settings: Settings, sessions: sessionmaker[Session]) -> int:
    """Fail the jobs of workers that stopped sending heartbeats."""
    count = 0
    for job in await app.job_manager.get_stalled_jobs(seconds_since_heartbeat=STALLED_SECONDS):
        operation = JOBS.get(job.task_name)
        assert job.id is not None
        try:
            await app.job_manager.finish_job(job, status=Status.FAILED, delete_job=False)
        except Exception:
            # Another worker recovered it first.
            continue
        if operation is not None and operation.job is not None:
            # Only the worker whose finish_job succeeded runs the hooks. One that fails is
            # logged, and the lost worker is still recorded below.
            try:
                await asyncio.to_thread(interrupt, operation, sessions, dict(job.task_kwargs))
                if operation.job.notify:
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
    if not ready.wait(30):
        raise TimeoutError("worker did not start")
    if "error" in state:
        raise state["error"]
    try:
        yield
    finally:
        state["loop"].call_soon_threadsafe(state["worker"].stop)
        thread.join(30)
