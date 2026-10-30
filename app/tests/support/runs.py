"""Run rows written straight to the tables, for a person with three conversations."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.infrastructure.access import Caller
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.database.metadata import metadata

runs, conversations = metadata.tables["agent_runs"], metadata.tables["conversations"]
notifications, inputs = metadata.tables["notifications"], metadata.tables["agent_run_inputs"]


@pytest.fixture
def person(settings, owner_engine):
    """A user with no roles or password, three conversations, and a caller with its own engine.
    Unlike people.person(), it yields the caller and the conversations."""
    users = metadata.tables["users"]
    with owner_engine.begin() as connection:
        user = connection.execute(
            users.insert()
            .values(username="task_" + uuid4().hex, display_name="Synthetic task account")
            .returning(users.c.id)
        ).scalar_one()
        threads = [
            connection.execute(
                conversations.insert()
                .values(owner_id=user, title="Simulated")
                .returning(conversations.c.id)
            ).scalar_one()
            for _ in range(3)
        ]
    engine = make_engine(settings)
    caller = Caller(user_id=user, sessions=make_sessions(engine), settings=settings)
    yield SimpleNamespace(caller=caller, threads=threads)
    engine.dispose()
    events = metadata.tables["agent_run_events"]
    with owner_engine.begin() as connection:
        connection.execute(notifications.delete().where(notifications.c.user_id == user))
        owned = select(runs.c.id).where(runs.c.started_by == user)
        connection.execute(events.delete().where(events.c.run_id.in_(owned)))
        connection.execute(inputs.delete().where(inputs.c.owner_id == user))
        connection.execute(runs.delete().where(runs.c.started_by == user))
        sessions_table = metadata.tables["agent_sessions"]
        connection.execute(
            sessions_table.delete().where(sessions_table.c.conversation_id.in_(threads))
        )
        connection.execute(conversations.delete().where(conversations.c.owner_id == user))
        connection.execute(users.delete().where(users.c.id == user))


def add_run(owner_engine, person, thread, status, mode, queued=timedelta(0)):
    """A run row with one segment queued `queued` ago."""
    identifier = uuid4()
    with owner_engine.begin() as connection:
        connection.execute(
            runs.insert().values(
                id=identifier,
                conversation_id=thread,
                started_by=person.caller.user_id,
                status=status,
                model="scripted",
                instructions_version="simulated",
                mode=mode,
            )
        )
        connection.execute(
            inputs.insert().values(
                run_id=identifier,
                segment=1,
                owner_id=person.caller.user_id,
                input={},
                created_at=datetime.now(UTC) - queued,
            )
        )
    return identifier
