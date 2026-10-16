"""Serialized start limits and durable finalization end before any model wait."""

from sqlalchemy.exc import IntegrityError

from app.agent import storage
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import Conflict, NotFound


def scope(caller):
    if caller.user_id is None or caller.sessions is None or caller.settings is None:
        raise NotFound()
    return caller.sessions


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
