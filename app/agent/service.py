"""Serialized start limits and durable finalization end before any model wait."""

from sqlalchemy.exc import IntegrityError

from app.agent import storage
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import Conflict, NotFound


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
            )
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise Conflict() from None
        raise


def resume(caller, request):
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
        storage.resume(session, caller.user_id, request.run_id)
    return state, {
        "interrupts": [
            {"id": pending[0]["interrupt"], "value": {"approved": approval.status == "approved"}}
        ]
    }


def finish(caller, request, status, state):
    with unit_of_work(scope(caller)) as session:
        storage.finish(session, caller.user_id, request.run_id, request.thread_id, status, state)


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
            datetime.now(UTC) + timedelta(seconds=120),
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


def queue_run(caller, request, read_only: bool) -> int:
    """Record the run's next input and queue it on the agent worker; returns its segment."""
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
        enqueue(session, caller, agent_run, job)
        return segment


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
    """Stop the run's job; one still queued ends here, a running one ends on its worker."""
    from app.jobs.handlers import active_job, cancel_job_by_id, job_state

    with unit_of_work(scope(caller)) as session:
        segment = storage.owned_segment(session, caller.user_id, run)
        if segment is None:
            raise NotFound()
        job = active_job(session, RUN_LOCK + str(run))
        if job is None:
            raise Conflict()
        queued = job_state(session, job) in {"queued", "scheduled"}
        cancel_job_by_id(session, job)
        if queued:
            storage.append_event(session, run, segment, CANCELLED)


# Worker side: sessions come from the worker, not a request.


def run_input(sessions, run, segment):
    with unit_of_work(sessions, read_only=True) as session:
        return storage.read_input(session, run, segment)


def append_event(sessions, run, segment, event) -> None:
    with unit_of_work(sessions) as session:
        storage.append_event(session, run, segment, event)


def interrupt(sessions, run, segment) -> None:
    with unit_of_work(sessions) as session:
        storage.interrupt(session, run)
        storage.append_event(session, run, segment, INTERRUPTED)


def prune_events(sessions) -> None:
    with unit_of_work(sessions) as session:
        storage.prune_events(session, 24)


CANCELLED = {"type": "RUN_ERROR", "code": "run_cancelled", "message": "Run stopped."}
INTERRUPTED = {
    "type": "RUN_ERROR",
    "code": "run_interrupted",
    "message": "The run was interrupted. Retry to run it again.",
}
