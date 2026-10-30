"""The agent_run job: the agent worker runs one segment of a chat turn and writes its events."""

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from uuid import UUID

import msgspec

from app.agent import service
from app.agent.handlers import RunAgentInput
from app.infrastructure.access import PUBLIC, SIGNED_IN, Caller
from app.infrastructure.errors import ApiError, NotFound
from app.infrastructure.operations import Job, operation
from app.infrastructure.operations.context import current_call
from app.infrastructure.shapes import Shape

TERMINAL = {"RUN_FINISHED", "RUN_ERROR", "CRAFT_ERROR"}


class AgentRunJob(Shape):
    run: UUID
    segment: int
    read_only: bool = False


@operation(PUBLIC)
def interrupt_run(caller: Caller, request: AgentRunJob) -> None:
    """End a run whose worker was lost: failed, with its stream's last event; a Python-only
    entry. A task-mode run also tells its owner."""
    service.interrupt(caller.sessions, request.run, request.segment)


def interrupted(sessions, request: AgentRunJob) -> None:
    """The agent_run job's hook when its worker died: the run ends through interrupt_run."""
    interrupt_run(Caller(sessions=sessions), request)


@operation(
    SIGNED_IN,
    job=Job(
        queue="agent",
        retry=False,
        lock=lambda request: service.RUN_LOCK + str(request.run),
        interrupted=interrupted,
    ),
)
async def agent_run(caller: Caller, request: AgentRunJob) -> None:
    """Run one queued segment of an owned chat turn and write its events."""
    from app.agent import runtime

    run, segment, sessions = request.run, request.segment, caller.sessions
    # The run is this logged call; its tool calls are child calls of it.
    context = current_call.get()
    assert context is not None
    found = await asyncio.to_thread(service.run_input, sessions, run, segment)
    if found is None:
        # Its input was pruned before a worker took it: the stream still ends.
        context.error_code = NotFound.code
        missing = {"type": "CRAFT_ERROR", "code": NotFound.code}
        await asyncio.to_thread(service.append_event, sessions, run, segment, missing)
        return
    if found.owner_id != caller.user_id:
        return
    data = msgspec.convert(found.input, RunAgentInput)
    ended = False

    async def write(event: dict) -> None:
        nonlocal ended
        await asyncio.to_thread(service.append_event, sessions, run, segment, event)
        ended = event["type"] in TERMINAL

    context.caller = replace(caller, conversation_id=data.thread_id, run_id=run)
    try:
        with runtime.read_only_query() if request.read_only else nullcontext():
            iterator = runtime.run(caller, data)
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
        await write(service.CANCELLED)
        raise
    except ApiError as error:
        # Raised before the run started (a conflict, a missing conversation): the POST
        # answers with that error.
        context.error_code = error.code
        await write({"type": "CRAFT_ERROR", "code": error.code})
    except Exception:
        context.error_code = ApiError.code
        await write({"type": "CRAFT_ERROR", "code": "internal_error"})
    if not ended:
        await write({"type": "CRAFT_ERROR", "code": "internal_error"})


@operation(PUBLIC, job=Job(queue="bulk", retry=False, every="*/15 * * * *"))
def prune_run_events(caller: Caller) -> None:
    """End runs left running with no job (their finish failed), then delete run events and
    inputs a day after their run ended."""
    assert caller.sessions is not None
    service.end_stalled(caller.sessions)
    service.prune_events(caller.sessions)
