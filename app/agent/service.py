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
