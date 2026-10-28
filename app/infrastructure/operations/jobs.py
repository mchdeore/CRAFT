"""Job operations: calling one queues it; a worker runs it checked and logged like any call.

The job's arguments hold its input and who asked, never secrets. A feature with large or private
input keeps it in its own table and queues its IDs (agent runs do). The worker's call is logged
as a child of the call that queued it, with the same request ID.
"""

import asyncio
import inspect
from contextvars import ContextVar
from time import perf_counter
from typing import Any
from uuid import UUID

import msgspec

from app.infrastructure.access import Caller
from app.infrastructure.errors import ApiError
from app.infrastructure.operations.context import CallContext, current_call
from app.infrastructure.operations.pipeline import begin, checked, decode, finish
from app.infrastructure.operations.registry import Operation
from app.infrastructure.shapes import Shape


class Queued(Shape):
    job_id: str


current_job: ContextVar[Any] = ContextVar("current_job", default=None)


def should_abort() -> bool:
    """Blocking jobs check this between steps; async jobs are cancelled instead."""
    job = current_job.get()
    return job is not None and job.should_abort()


def enqueue(session: Any, caller: Caller, operation: Any, request: Any = None) -> int:
    """Queue a job operation in the caller's transaction; a held lock returns the active job."""
    from app.jobs.service import defer

    declared: Operation = getattr(operation, "__operation__", operation)
    if declared.job is None:
        raise ValueError("Not a job operation")
    call = current_call.get()
    args = {
        "input": msgspec.to_builtins(request) if declared.input else None,
        "caller": {
            "user": str(caller.user_id) if caller.user_id else None,
            "roles": sorted(caller.roles),
            "auth_session": str(caller.auth_session_id) if caller.auth_session_id else None,
            "origin": caller.origin,
        },
        "parent": {"request": str(call.request_id), "call": str(call.call_id)} if call else None,
    }
    return defer(session, declared.name, args, caller.user_id)


def deferring(declared: Operation) -> Any:
    """The declared function's stand-in: queue the job in its own transaction."""
    from app.infrastructure.database.unit_of_work import unit_of_work

    def deferred(caller: Caller, request: Any = None) -> Queued:
        assert caller.sessions is not None
        with unit_of_work(caller.sessions) as session:
            return Queued(job_id=str(enqueue(session, caller, declared, request)))

    deferred.__name__, deferred.__doc__ = declared.function.__name__, declared.function.__doc__
    return deferred


def _input(declared: Operation, value: Any) -> Any:
    return decode(declared, None if value is None else msgspec.json.encode(value))


def _start(job: Any, caller: dict, parent: dict | None) -> tuple[Any, CallContext, Any, Caller]:
    rebuilt = Caller(
        user_id=UUID(caller["user"]) if caller["user"] else None,
        roles=frozenset(caller["roles"]),
        auth_session_id=UUID(caller["auth_session"]) if caller["auth_session"] else None,
        sessions=job.sessions,
        settings=job.settings,
        origin=caller["origin"],
    )
    outer = current_call.set(
        CallContext(UUID(parent["request"]), UUID(parent["call"]), None, rebuilt, perf_counter())
        if parent
        else None
    )
    ctx, token = begin(rebuilt)
    return outer, ctx, token, rebuilt


def runner(declared: Operation) -> Any:
    """The worker's entry: rebuild the caller, check and run the operation, log the call."""
    fn = declared.run

    def invoke(resolved: Caller, request: Any) -> Any:
        return fn(resolved, request) if declared.input else fn(resolved)

    if inspect.iscoroutinefunction(fn):

        async def run_async(job: Any, input: Any, caller: dict, parent: dict | None) -> None:
            outer, ctx, token, rebuilt = _start(job, caller, parent)
            running = current_job.set(job)
            error = None
            try:
                resolved, request = checked(declared, rebuilt, _input(declared, input))
                await invoke(resolved, request)
            except ApiError as failure:
                error = failure
                raise
            except BaseException:
                # The original error stays raised, so the queue can tell transient from permanent.
                error = ApiError()
                raise
            finally:
                current_job.reset(running)
                current_call.reset(token)
                await asyncio.to_thread(finish, ctx, None, declared.name, "operation", error)
                current_call.reset(outer)

        return run_async

    def run_sync(job: Any, input: Any, caller: dict, parent: dict | None) -> None:
        outer, ctx, token, rebuilt = _start(job, caller, parent)
        running = current_job.set(job)
        error = None
        try:
            resolved, request = checked(declared, rebuilt, _input(declared, input))
            invoke(resolved, request)
        except ApiError as failure:
            error = failure
            raise
        except BaseException:
            error = ApiError()
            raise
        finally:
            current_job.reset(running)
            current_call.reset(token)
            finish(ctx, None, declared.name, "operation", error)
            current_call.reset(outer)

    return run_sync


def register(declared: Operation) -> None:
    from app.infrastructure.queue import register_job

    job = declared.job
    assert job is not None
    lock, interrupted = job.lock, job.interrupted
    register_job(
        declared.name,
        runner(declared),
        queue=job.queue,
        timeout=job.timeout,
        retry=job.retry,
        lock_key=(lambda args: lock(_input(declared, args["input"]))) if lock else None,
        interrupted=(
            (lambda sessions, args: interrupted(sessions, _input(declared, args["input"])))
            if interrupted
            else None
        ),
    )
