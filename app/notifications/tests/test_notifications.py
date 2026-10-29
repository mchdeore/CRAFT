"""A person reads and marks only their own notifications."""

from uuid import uuid4

import pytest

from app.infrastructure.access import Caller
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.database.metadata import metadata
from app.infrastructure.errors import NotFound
from app.notifications.handlers import (
    NotificationPageRequest,
    NotificationRef,
    list_notifications,
    notify,
    read_notification,
)


@pytest.fixture
def people(settings, owner_engine):
    users = metadata.tables["users"]
    with owner_engine.begin() as connection:
        ids = [
            connection.execute(
                users.insert()
                .values(username="notified_" + uuid4().hex, display_name="Synthetic account")
                .returning(users.c.id)
            ).scalar_one()
            for _ in range(2)
        ]
    engine = make_engine(settings)
    sessions = make_sessions(engine)
    yield [Caller(user_id=user, roles=frozenset({"member"}), sessions=sessions) for user in ids]
    engine.dispose()
    notifications = metadata.tables["notifications"]
    with owner_engine.begin() as connection:
        connection.execute(notifications.delete().where(notifications.c.user_id.in_(ids)))
        connection.execute(users.delete().where(users.c.id.in_(ids)))


def test_notifications_are_listed_and_marked_read_by_their_owner(people):
    mine, theirs = people
    assert mine.sessions is not None
    with mine.sessions.begin() as session:
        notify(session, mine.user_id, "job_done", "job", "41")
        notify(session, mine.user_id, "run_waiting", "run", str(uuid4()))
        notify(session, theirs.user_id, "job_failed", "job", "42")
    listed = list_notifications(mine, NotificationPageRequest())
    assert [item.kind for item in listed] == ["run_waiting", "job_done"]
    assert all(item.read_at is None for item in listed)

    read = read_notification(mine, NotificationRef(id=listed[1].id))
    assert read.read_at is not None
    assert read_notification(mine, NotificationRef(id=listed[1].id)).read_at == read.read_at
    unread = list_notifications(mine, NotificationPageRequest(unread=True))
    assert [item.id for item in unread] == [listed[0].id]

    other = list_notifications(theirs, NotificationPageRequest())[0]
    with pytest.raises(NotFound):
        read_notification(mine, NotificationRef(id=other.id))
    assert list_notifications(theirs, NotificationPageRequest())[0].read_at is None
