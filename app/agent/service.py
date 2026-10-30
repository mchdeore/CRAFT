"""Serialized start limits and durable finalization end before any model wait."""

from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.agent import storage
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import Conflict, NotFound

# A run credential outlives any run; the run revokes it when it ends.
TOKEN_SECONDS = storage.RUN_SECONDS + 60


def scope(caller):
    if caller.user_id is None or caller.sessions is None or caller.settings is None:
        raise NotFound()
    return caller.sessions


def recovery(caller, request):
    from app.agent.handlers import RunRecovery

    with unit_of_work(scope(caller)) as session:
        row = storage.recovery(session, caller.user_id, request.id)
        if row is None:
            raise NotFound()
        status = row["status"] or "none"
        approval_id = row["approval_id"]
        approval_status = row["approval_status"] if approval_id is not None else None
        return RunRecovery(
            status=status,
            run_id=row["run_id"],
            started_at=row["started_at"].isoformat() if row["started_at"] is not None else None,
            ended_at=row["ended_at"].isoformat() if row["ended_at"] is not None else None,
            approval_id=approval_id,
            approval_status=approval_status,
            can_resume=status == "paused" and approval_status in {"approved", "rejected"},
            last_event=storage.last_event(session, row["run_id"]) if row["run_id"] else None,
        )


def start(caller, request, version):
    try:
        with unit_of_work(scope(caller)) as session:
            return storage.start(
                session,
                caller.user_id,
                request.thread_id,
                request.run_id,
                caller.settings.model_main,
                version,
                request.mode,
            )
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise Conflict() from None
        raise


def resume(caller, request):
    """The paused run's state, the answer to its approval, and its mode."""
    from uuid import UUID

    from app.approvals.handlers import ApprovalRef, read_approval

    payload = request.resume
    if not isinstance(payload, dict) or set(payload) != {"approvalId"}:
        raise Conflict()
    try:
        identifier = UUID(payload["approvalId"])
    except (ValueError, TypeError, AttributeError):
        raise Conflict() from None
    approval = read_approval(caller, ApprovalRef(id=identifier))
    if approval.run_id != request.run_id or approval.conversation_id != request.thread_id:
        raise NotFound()
    if approval.status not in {"approved", "rejected"}:
        raise Conflict()
    with unit_of_work(scope(caller)) as session:
        state = storage.paused_state(session, caller.user_id, request.run_id, request.thread_id)
        pending = state.get("state", {}).get("craftApprovals", [])
        if (
            len(pending) != 1
            or pending[0].get("id") != str(identifier)
            or not pending[0].get("interrupt")
        ):
            raise Conflict()
        mode = storage.resume(session, caller.user_id, request.run_id)
    answer = {"approved": approval.status == "approved"}
    return state, {"interrupts": [{"id": pending[0]["interrupt"], "value": answer}]}, mode


def finish(
    caller, request, status, state, *, tool_calls=None, active_seconds=None, end_reason=None
):
    with unit_of_work(scope(caller)) as session:
        mode = storage.finish(
            session,
            caller.user_id,
            request.run_id,
            request.thread_id,
            status,
            state,
            tool_calls=tool_calls,
            active_seconds=active_seconds,
            end_reason=end_reason,
        )
        if mode == "task" and status != "cancelled":
            _notify(session, caller.user_id, request.run_id, status)


def _notify(session, user, run, status) -> None:
    """A task-mode run tells its owner, who is likely away, that it ended or waits."""
    from app.notifications.handlers import notify

    notify(session, user, "run_waiting" if status == "paused" else "run_done", "run", str(run))


def issue_token(caller, identifier):
    import base64
    import hashlib
    import secrets
    from datetime import UTC, datetime, timedelta

    from app.infrastructure.operations.context import current_call

    raw = secrets.token_bytes(32)
    parent = current_call.get()
    context = {
        "runId": str(identifier),
        "requestId": str(parent.request_id) if parent else None,
        "parentCallId": str(parent.parent_call_id or parent.call_id) if parent else None,
        "authSessionId": str(caller.auth_session_id) if caller.auth_session_id else None,
    }
    with unit_of_work(scope(caller)) as session:
        storage.issue_token(
            session,
            caller.user_id,
            identifier,
            hashlib.sha256(raw).digest(),
            datetime.now(UTC) + timedelta(seconds=TOKEN_SECONDS),
            context,
        )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def revoke_token(caller, identifier):
    with unit_of_work(scope(caller)) as session:
        storage.revoke_token(session, caller.user_id, identifier)


def principal(caller, digest):
    from datetime import UTC, datetime
    from uuid import UUID

    from app.infrastructure.errors import NotSignedIn

    if caller.sessions is None:
        raise NotSignedIn()
    with unit_of_work(caller.sessions, read_only=True) as session:
        row = storage.principal(session, digest, datetime.now(UTC))
    if row is None:
        raise NotSignedIn()
    state = row["state"]
    context = state.get("craftTransport", {}) if isinstance(state, dict) else {}
    if not isinstance(context, dict):
        context = {}
    if context.get("runId") != str(row["id"]):
        context = {}

    def identifier(name):
        value = context.get(name)
        return UUID(value) if value else None

    return {
        "user": row["started_by"],
        "conversation": row["conversation_id"],
        "run": row["id"],
        "roles": row["roles"],
        "request_id": identifier("requestId"),
        "parent_call_id": identifier("parentCallId"),
        "auth_session_id": identifier("authSessionId"),
    }


RUN_LOCK = "agent-run:"


def queue_run(caller, request, read_only: bool) -> tuple[int, int]:
    """Record the run's next input and queue it on the agent worker; returns its segment and
    job."""
    import msgspec

    from app.agent.jobs import AgentRunJob, agent_run
    from app.infrastructure.operations.jobs import enqueue
    from app.jobs.handlers import active_job

    with unit_of_work(scope(caller)) as session:
        if active_job(session, RUN_LOCK + str(request.run_id)) is not None:
            raise Conflict()
        segment = storage.add_segment(
            session, caller.user_id, request.run_id, request.thread_id, msgspec.to_builtins(request)
        )
        job = AgentRunJob(run=request.run_id, segment=segment, read_only=read_only)
        return segment, enqueue(session, caller, agent_run, job)


def asked_job(caller, job_id: int) -> tuple[str, UUID, int] | None:
    """The state, run and segment of the caller's own ask_agent job: a read-only agent run.

    None for anyone else's job, a chat turn or any other kind of job."""
    import msgspec

    from app.agent.jobs import AgentRunJob, agent_run
    from app.jobs.handlers import requested_job

    with unit_of_work(scope(caller), read_only=True) as session:
        found = requested_job(session, job_id, agent_run.__operation__.name, caller.user_id)
    if found is None:
        return None
    state, arguments = found
    job = msgspec.convert(arguments, AgentRunJob)
    return (state, job.run, job.segment) if job.read_only else None


def owned_segment(caller, run) -> int:
    with unit_of_work(scope(caller), read_only=True) as session:
        segment = storage.owned_segment(session, caller.user_id, run)
    if segment is None:
        raise NotFound()
    return segment


def events_after(caller, run, segment, after):
    with unit_of_work(scope(caller), read_only=True) as session:
        return storage.events_after(session, run, segment, after)


def run_job_state(caller, run) -> str | None:
    """The state of the run's queued or running job; None once it has ended."""
    from app.jobs.handlers import active_job, job_state

    with unit_of_work(scope(caller)) as session:
        job = active_job(session, RUN_LOCK + str(run))
        return job_state(session, job) if job is not None else None


def cancel_run(caller, run) -> None:
    """Stop the run's job; one still queued ends here, a running one ends on its worker. A
    paused run, which has no job, ends here too, and its pending approval is rejected."""
    from app.approvals.handlers import reject_pending
    from app.jobs.handlers import active_job, cancel_job_by_id

    with unit_of_work(scope(caller)) as session:
        # Owned through the run itself: prune may have removed a paused run's segments.
        if storage.cancel_paused(session, caller.user_id, run):
            reject_pending(session, caller.user_id, run)
            segment = storage.owned_segment(session, caller.user_id, run)
            if segment is not None:
                storage.append_event(session, run, segment, CANCELLED)
            return
        segment = storage.owned_segment(session, caller.user_id, run)
        if segment is None:
            raise NotFound()
        job = active_job(session, RUN_LOCK + str(run))
        if job is None:
            raise Conflict()
        # Deciding "still queued" and cancelling in one statement means a worker
        # that claims the job meanwhile is stopped instead and writes the end itself.
        if not _cancel_queued(session, run, segment, job):
            cancel_job_by_id(session, job)


def cancel_queued_run(caller, run) -> bool:
    """End the run as cancel_run does, but only while its job is still queued."""
    from app.jobs.handlers import active_job

    with unit_of_work(scope(caller)) as session:
        segment = storage.owned_segment(session, caller.user_id, run)
        job = active_job(session, RUN_LOCK + str(run))
        if segment is None or job is None:
            return False
        return _cancel_queued(session, run, segment, job)


def _cancel_queued(session, run, segment, job) -> bool:
    from app.jobs.handlers import cancel_queued_job

    if not cancel_queued_job(session, job):
        return False
    storage.append_event(session, run, segment, CANCELLED)
    return True


# Worker side: sessions come from the worker, not a request.


def run_input(sessions, run, segment):
    with unit_of_work(sessions, read_only=True) as session:
        return storage.read_input(session, run, segment)


def append_event(sessions, run, segment, event) -> None:
    with unit_of_work(sessions) as session:
        storage.append_event(session, run, segment, event)


def interrupt(sessions, run, segment) -> None:
    with unit_of_work(sessions) as session:
        ended = storage.interrupt(session, run)
        if ended is not None and ended.mode == "task":
            _notify(session, ended.started_by, run, "failed")
        storage.append_event(session, run, segment, INTERRUPTED)


def end_stalled(sessions) -> None:
    """A run left running with no job (its finish failed) ends failed, which frees its
    conversation. A live run's job holds its lock, so it is never ended here."""
    from app.jobs.handlers import active_job

    with unit_of_work(sessions) as session:
        for run in storage.stalled(session):
            if active_job(session, RUN_LOCK + str(run)) is None:
                storage.interrupt(session, run)


def prune_events(sessions) -> None:
    with unit_of_work(sessions) as session:
        storage.prune_events(session, 24)


CANCELLED = {"type": "RUN_ERROR", "code": "run_cancelled", "message": "Run stopped."}
INTERRUPTED = {
    "type": "RUN_ERROR",
    "code": "run_interrupted",
    "message": "The run was interrupted. Retry to run it again.",
}
