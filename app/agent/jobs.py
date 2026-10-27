"""The agent_run job: the agent worker runs one segment of a chat turn and writes its events."""

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from time import perf_counter
from uuid import UUID, uuid4

import msgspec

from app.agent import service
from app.agent.handlers import RunAgentInput
from app.infrastructure.access import Caller
from app.infrastructure.errors import ApiError
from app.infrastructure.operations.context import CallContext, current_call
from app.infrastructure.operations.pipeline import finish
from app.infrastructure.queue import JobRun, register_job, register_maintenance

TERMINAL = {"RUN_FINISHED", "RUN_ERROR", "CRAFT_ERROR"}


async def agent_run(
    job: JobRun,
    user: str,
    run: str,
    segment: int,
    roles: list[str],
    auth_session_id: str | None,
    origin: str,
    read_only: bool,
    request_id: str | None = None,
    parent_call_id: str | None = None,
) -> None:
    from app.agent import runtime

    found = await asyncio.to_thread(service.run_input, job.sessions, UUID(run), segment)
    if found is None or str(found.owner_id) != user:
        return
    request = msgspec.convert(found.input, RunAgentInput)
    caller = Caller(
        user_id=UUID(user),
        roles=frozenset(roles),
        auth_session_id=UUID(auth_session_id) if auth_session_id else None,
        sessions=job.sessions,
        settings=job.settings,
        origin=origin,
    )
    ended = False

    async def write(event: dict) -> None:
        nonlocal ended
        await asyncio.to_thread(service.append_event, job.sessions, UUID(run), segment, event)
        ended = event["type"] in TERMINAL

    # The run is one logged operation; its tool calls are child calls of it.
    context = CallContext(
        UUID(request_id) if request_id else uuid4(),
        uuid4(),
        UUID(parent_call_id) if parent_call_id else None,
        replace(caller, conversation_id=request.thread_id, run_id=UUID(run)),
        perf_counter(),
    )
    token = current_call.set(context)
    failure = None
    try:
        with runtime.read_only_query() if read_only else nullcontext():
            iterator = runtime.run(caller, request)
            # RUN_FINISHED and RUN_ERROR wait until the runtime has saved the run, so a client
            # that sees them also sees the run's final status.
            final = None
            try:
                async for event in iterator:
                    value = msgspec.to_builtins(event)
                    if value["type"] in TERMINAL:
                        final = value
                    else:
                        await write(value)
            finally:
                await iterator.aclose()
            if final is not None:
                await write(final)
    except asyncio.CancelledError:
        # Stopped through the cancel route; the runtime has already saved the run as cancelled.
        failure = ApiError()
        await write(service.CANCELLED)
        raise
    except ApiError as error:
        # Raised before the run started (a conflict, a missing conversation): the POST
        # answers with that error.
        failure = error
        await write({"type": "CRAFT_ERROR", "code": error.code})
    except Exception:
        failure = ApiError()
        await write({"type": "CRAFT_ERROR", "code": "internal_error"})
    finally:
        current_call.reset(token)
        await asyncio.to_thread(finish, context, None, "agent.agent_run", "operation", failure)
    if not ended:
        await write({"type": "CRAFT_ERROR", "code": "internal_error"})


def interrupted(sessions, args: dict) -> None:
    service.interrupt(sessions, UUID(args["run"]), args["segment"])


register_job(
    "agent_run",
    agent_run,
    queue="agent",
    retry=False,
    lock_key=lambda args: service.RUN_LOCK + args["run"],
    interrupted=interrupted,
)
register_maintenance(service.prune_events)
