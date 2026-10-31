"""A person reads and marks only their own notifications."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.infrastructure.access import Caller
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.database.metadata import metadata
from app.infrastructure.errors import NotFound
from app.notifications.handlers import (
    JobEnded,
    NotificationPageRequest,
    NotificationRef,
    job_ended,
    list_notifications,
    notify,
    read_notification,
)
from app.tests.support.cube import cube_source as cube_source


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


def test_a_job_notifies_once_and_the_write_is_a_logged_call(people, owner_engine):
    from sqlalchemy import func

    mine, _ = people
    job = 10**12 + uuid4().int % 10**9
    call_log = metadata.tables["call_log"]
    with owner_engine.connect() as connection:
        before = connection.execute(select(func.max(call_log.c.id))).scalar_one() or 0
    for failed in (False, True):
        # A worker lost after the first notification can't add a second for the same job.
        job_ended(
            Caller(sessions=mine.sessions), JobEnded(user=mine.user_id, job_id=job, failed=failed)
        )
    listed = list_notifications(mine, NotificationPageRequest())
    assert [(item.kind, item.ref_id) for item in listed] == [("job_done", str(job))]
    with owner_engine.connect() as connection:
        outcomes = connection.execute(
            select(call_log.c.outcome).where(
                call_log.c.name == "notifications.job_ended", call_log.c.id > before
            )
        ).scalars()
        # Both calls are logged, though only the first wrote a notification.
        assert list(outcomes) == ["ok", "ok"]


def test_notification_routes_refuse_bad_requests(client, cube_source):
    assert client.get("/api/notifications").status_code == 401
    with TestClient(cube_source["app"], base_url="https://testserver") as http:
        cube_source["sign_in"](http)
        assert http.get("/api/notifications?limit=0").status_code == 400
        assert http.get("/api/notifications?limit=101").status_code == 400
        assert http.get("/api/notifications?unread=true").json() == []
        assert http.get("/api/notifications?unread=maybe").status_code == 400
        origin = {"Origin": "https://testserver"}
        marker = "synthetic-content-" + uuid4().hex
        refused = http.post(f"/api/notifications/{marker}/read", headers=origin)
        # The error body names the problem, never the input.
        assert refused.status_code == 400 and marker not in refused.text
        assert http.post(f"/api/notifications/{uuid4()}/read", headers=origin).status_code == 404
        # A write without the browser's Origin is refused.
        assert http.post(f"/api/notifications/{uuid4()}/read").status_code == 403
