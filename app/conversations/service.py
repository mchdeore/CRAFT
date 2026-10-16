"""Ownership is checked in each short conversation transaction."""

from sqlalchemy import select

from app.conversations import storage
from app.infrastructure.database import metadata as m
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import NotFound


def scope(caller):
    if caller.user_id is None or caller.sessions is None:
        raise NotFound()
    return caller.sessions


def load(caller, identifier):
    with unit_of_work(scope(caller), read_only=True) as session:
        if storage.owned(session, caller.user_id, identifier) is None:
            raise NotFound()
        return storage.history(session, identifier)


def append(caller, request):
    with unit_of_work(scope(caller)) as session:
        if storage.owned(session, caller.user_id, request.id) is None:
            raise NotFound()
        run = session.execute(
            select(m.metadata.tables["agent_runs"].c.id).where(
                m.metadata.tables["agent_runs"].c.id == request.run,
                m.metadata.tables["agent_runs"].c.conversation_id == request.id,
                m.metadata.tables["agent_runs"].c.started_by == caller.user_id,
            )
        ).first()
        if run is None:
            raise NotFound()
        storage.append(session, request.id, request.run, request.messages)
