"""Queued questions: ask_agent queues a read-only run and get_answer reads its answer later.

The run is an ordinary agent_run job on the agent queue, so it shows in GET /api/jobs. Its
answer is assembled from the run's events, which are kept for a day.
"""

from dataclasses import replace
from uuid import UUID, uuid4

from app.agent import service
from app.infrastructure.errors import ApiError, NotFound
from app.infrastructure.operations.context import current_call

# Job states before the end (app.jobs.handlers.JobState): no answer yet is still pending.
ACTIVE = {"queued", "scheduled", "started", "stopping"}


def queue(caller, question: str, conversation: UUID | None) -> tuple[int, UUID]:
    """Queue a read-only run for the question in an owned or new conversation; returns its job
    and conversation. The calling operation is logged with the conversation and run."""
    from app.agent.handlers import RunAgentInput
    from app.conversations.handlers import NewConversation, create_conversation

    thread = conversation or create_conversation(caller, NewConversation()).id
    run = uuid4()
    trusted = replace(caller, conversation_id=thread, run_id=run)
    context = current_call.get()
    if context is not None:
        context.caller = trusted
    data = RunAgentInput(
        thread_id=thread,
        run_id=run,
        messages=[{"id": str(uuid4()), "role": "user", "content": question}],
    )
    _, job = service.queue_run(trusted, data, True)
    return job, thread


def collect(events) -> dict | None:
    """The answer in a run's events once it finished; None before. A failed run raises."""
    from app.agent.runs import error_for

    text, answer = "", None
    for event in events:
        kind = event["type"]
        if kind == "TEXT_MESSAGE_START":
            text = ""
        elif kind == "TEXT_MESSAGE_CONTENT":
            text += event["delta"]
        elif kind == "ACTIVITY_SNAPSHOT" and event.get("activityType") == "craft.answer":
            answer = event["content"]
        elif kind in {"CRAFT_ERROR", "RUN_ERROR"}:
            raise error_for(event.get("code") or "")
        elif kind == "RUN_FINISHED":
            if answer is None:
                raise ApiError()
            return {"answer": text, **answer}
    return None


def read(caller, job_id: int) -> tuple[dict | None, UUID]:
    """The answer of the caller's own ask_agent job, or None while it is queued or running."""
    found = service.asked_job(caller, job_id)
    if found is None:
        raise NotFound()
    state, run, segment = found
    events, after = [], 0
    while page := service.events_after(caller, run, segment, after):
        events.extend(event for _, event in page)
        after = page[-1][0]
    result = collect(events)
    if result is None and state not in ACTIVE:
        # A finished run's events are kept for a day; a run stopped before its end has none.
        raise NotFound() if state == "finished" else ApiError()
    return result, run
