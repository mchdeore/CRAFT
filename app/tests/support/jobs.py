"""Test-only job kinds; `python -m app.tests.support.jobs` runs a worker that knows them."""

import asyncio
import time

from procrastinate.exceptions import JobAborted

from app.infrastructure.queue import (
    KINDS,
    JobRun,
    PermanentError,
    TransientError,
    register_job,
    run_worker,
)


async def sleep(job: JobRun, seconds: float = 0, fail: str | None = None, times: int = 1) -> None:
    """Sleep on the first attempt only; fail `times` attempts transiently or permanently."""
    if job.attempt == 1:
        await asyncio.sleep(seconds)
    if fail == "transient" and job.attempt <= times:
        raise TransientError(retry_after=0)
    if fail == "permanent":
        raise PermanentError()


def blocking(job: JobRun, seconds: float = 0) -> None:
    """A blocking job that stops between steps when asked to."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if job.should_abort():
            raise JobAborted()
        time.sleep(0.05)


def register() -> None:
    if "test.sleep" not in KINDS:
        register_job(
            "test.sleep", sleep, queue="interactive", lock_key=lambda a: a.get("key") or ""
        )
        register_job("test.bulk", sleep, queue="bulk")
        register_job("test.agent", sleep, queue="agent", retry=False)
        register_job("test.blocking", blocking, queue="interactive")


if __name__ == "__main__":
    from app.infrastructure.config.settings import Settings

    register()
    run_worker(Settings.from_env())
