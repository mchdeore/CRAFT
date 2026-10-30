"""Chat turns run on the agent worker; the app streams their events from the database.

POST /api/agent/runs queues the turn and follows its events; a closed connection no longer
stops it. A browser that left re-attaches with GET /api/agent/runs/{run}/events?after=N.
"""

import asyncio
import weakref
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

import msgspec
import psycopg
from starlette.concurrency import run_in_threadpool

from app.agent import service
from app.agent.handlers import AGUIEvent
from app.agent.storage import EVENTS_CHANNEL, RUN_SECONDS
from app.infrastructure.database.engine import conninfo
from app.infrastructure.errors import ApiError
from app.infrastructure.operations.context import current_call

# Longer than any run plus time waiting for a worker.
FOLLOW_SECONDS = RUN_SECONDS + 300
# Without a notification, look again this often; every CHECK_EVERY looks, check the job.
POLL_SECONDS = 1.0
CHECK_EVERY = 5
# A run no agent worker has taken by then fails rather than waiting.
QUEUED_SECONDS = 60
TERMINAL = {"RUN_FINISHED", "RUN_ERROR"}


def error_for(code: str) -> ApiError:
    def classes(base):
        for sub in base.__subclasses__():
            yield sub
            yield from classes(sub)

    return next((cls() for cls in classes(ApiError) if cls.code == code), ApiError())


class Listener:
    """One LISTEN connection per event loop wakes every stream following a run."""

    _loops: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Listener]" = (
        weakref.WeakKeyDictionary()
    )

    def __init__(self, settings) -> None:
        self.settings = settings
        self.waiters: dict[str, set[asyncio.Event]] = {}
        self.task: asyncio.Task | None = None

    @classmethod
    def current(cls, settings) -> "Listener":
        loop = asyncio.get_running_loop()
        listener = cls._loops.get(loop)
        if listener is None:
            listener = cls._loops[loop] = cls(settings)
        return listener

    async def _listen(self) -> None:
        try:
            connection = await psycopg.AsyncConnection.connect(
                conninfo(self.settings), autocommit=True
            )
            async with connection:
                await connection.execute(f"LISTEN {EVENTS_CHANNEL}")
                async for notice in connection.notifies():
                    for waiter in self.waiters.get(notice.payload, ()):
                        waiter.set()
        except psycopg.Error:
            pass  # streams keep polling; the next subscriber starts listening again

    @asynccontextmanager
    async def subscribe(self, run) -> AsyncIterator[asyncio.Event]:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._listen())
        signal = asyncio.Event()
        self.waiters.setdefault(str(run), set()).add(signal)
        try:
            yield signal
        finally:
            waiters = self.waiters.get(str(run), set())
            waiters.discard(signal)
            if not waiters:
                self.waiters.pop(str(run), None)
            if not self.waiters and self.task is not None:
                self.task.cancel()
                self.task = None


def _note(caller, run=None, code=None) -> None:
    context = current_call.get()
    if context is None:
        return
    if run is not None:
        context.caller = replace(context.caller, run_id=run)
    if code is not None:
        context.error_code = code


async def follow(caller, run, segment, after, *, raise_errors) -> AsyncGenerator[AGUIEvent]:
    """Events of one segment after `after`, live until the run finishes, fails or stops."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + FOLLOW_SECONDS
    idle = 0
    sent = False
    async with Listener.current(caller.settings).subscribe(run) as signal:
        while True:
            signal.clear()
            rows = await run_in_threadpool(service.events_after, caller, run, segment, after)
            for seq, event in rows:
                after = seq
                if event["type"] == "CRAFT_ERROR":
                    _note(caller, code=event["code"])
                    error = error_for(event["code"])
                    if raise_errors:
                        raise error
                    yield AGUIEvent(type="RUN_ERROR", code=error.code, message=type(error).message)
                    return
                if event["type"] == "RUN_ERROR":
                    _note(caller, code=event.get("code"))
                yield msgspec.convert(event, AGUIEvent)
                sent = True
                if event["type"] in TERMINAL:
                    return
            if rows:
                idle = 0
                continue
            idle += 1
            lost = loop.time() > deadline
            if idle % CHECK_EVERY == 0:
                state = await run_in_threadpool(service.run_job_state, caller, run)
                ended = state is None
                if ended and not await run_in_threadpool(
                    service.events_after, caller, run, segment, after
                ):
                    # The job ended (or was cancelled) with nothing more to read.
                    lost = True
                if state in {"queued", "scheduled"} and loop.time() - started > QUEUED_SECONDS:
                    # No worker took it: cancel it while still queued, which writes the
                    # CANCELLED event the next read ends with. A worker that took it
                    # meanwhile runs it, and this keeps following.
                    if await run_in_threadpool(service.cancel_queued_run, caller, run):
                        continue
            if lost:
                # Before its first event the request fails; after it, the stream still ends
                # with a terminal event.
                if not sent:
                    raise ApiError()
                _note(caller, code=ApiError.code)
                yield AGUIEvent(type="RUN_ERROR", code=ApiError.code, message=ApiError.message)
                return
            try:
                await asyncio.wait_for(signal.wait(), POLL_SECONDS)
            except TimeoutError:
                pass


async def stream(caller, request, read_only: bool) -> AsyncGenerator[AGUIEvent]:
    """Queue the run (a start or a resume) and stream it from its first event."""
    segment, _ = await run_in_threadpool(service.queue_run, caller, request, read_only)
    _note(caller, run=request.run_id)
    async for event in follow(caller, request.run_id, segment, 0, raise_errors=True):
        yield event


async def reattach(caller, run, after) -> AsyncGenerator[AGUIEvent]:
    segment = await run_in_threadpool(service.owned_segment, caller, run)
    _note(caller, run=run)
    async for event in follow(caller, run, segment, after, raise_errors=False):
        yield event
