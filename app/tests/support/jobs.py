"""Test-only job operations; `python -m app.tests.support.job_worker` runs them."""

import asyncio
import time

from procrastinate.exceptions import JobAborted

from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import Job, operation
from app.infrastructure.operations.jobs import current_job, report_progress, should_abort
from app.infrastructure.queue import PermanentError, TransientError
from app.infrastructure.shapes import Shape


class Sleep(Shape):
    seconds: float = 0
    fail: str | None = None
    times: int = 1
    key: str = ""


@operation(SIGNED_IN, job=Job(queue="interactive", lock=lambda request: request.key))
async def sleep(caller: Caller, request: Sleep) -> None:
    """Sleep on the first attempt only; fail `times` attempts transiently or permanently."""
    attempt = current_job.get().attempt
    if attempt == 1:
        await asyncio.sleep(request.seconds)
    if request.fail == "transient" and attempt <= request.times:
        raise TransientError(retry_after=0)
    if request.fail == "permanent":
        raise PermanentError()


@operation(SIGNED_IN, job=Job(queue="bulk"))
async def bulk(caller: Caller, request: Sleep) -> None:
    """Sleep in the bulk queue."""
    await asyncio.sleep(request.seconds)


@operation(SIGNED_IN, job=Job(queue="agent", retry=False))
async def agent(caller: Caller) -> None:
    """Finish at once in the agent queue."""


@operation(SIGNED_IN, job=Job(queue="interactive"))
def blocking(caller: Caller, request: Sleep) -> None:
    """A blocking job that stops between steps when asked to."""
    deadline = time.monotonic() + request.seconds
    while time.monotonic() < deadline:
        if should_abort():
            raise JobAborted()
        time.sleep(0.05)


@operation("accounts.manage", job=Job(queue="interactive"))
def managed(caller: Caller) -> None:
    """Only a manager's job may run."""


class Count(Shape):
    total: int
    fail: bool = False
    seconds: float = 0


@operation(SIGNED_IN, job=Job(queue="interactive", retry=False, notify=True))
def counted(caller: Caller, request: Count) -> None:
    """Report progress on every step, wait `seconds`, then finish or fail when asked to."""
    for done in range(1, request.total + 1):
        report_progress(done, request.total, "Counting")
    time.sleep(request.seconds)
    if request.fail:
        raise PermanentError()
