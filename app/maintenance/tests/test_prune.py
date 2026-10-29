"""The daily prune deletes only rows past their keep time."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.infrastructure.access import Caller
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.database.metadata import metadata
from app.maintenance.jobs import prune


def test_prune_deletes_expired_and_old_rows_only(settings, owner_engine):
    tables = metadata.tables
    users, sessions_table, tokens = (
        tables["users"],
        tables["auth_sessions"],
        tables["workspace_tokens"],
    )
    progress, notifications = tables["job_progress"], tables["notifications"]
    now = datetime.now(UTC)
    old_job, new_job = (-(10**12) - uuid4().int % 10**9 for _ in range(2))
    with owner_engine.begin() as connection:
        user = connection.execute(
            users.insert()
            .values(username="pruned_" + uuid4().hex, display_name="Synthetic account")
            .returning(users.c.id)
        ).scalar_one()
        for expires in (now - timedelta(hours=1), now + timedelta(hours=1)):
            connection.execute(
                sessions_table.insert().values(
                    user_id=user,
                    token_sha256=os.urandom(32),
                    idle_expires_at=min(expires, now),
                    expires_at=expires,
                )
            )
            connection.execute(
                tokens.insert().values(
                    user_id=user, token_sha256=os.urandom(32), label="Synthetic", expires_at=expires
                )
            )
        connection.execute(
            progress.insert(),
            [
                {"job_id": old_job, "done": 1, "total": 2, "updated_at": now - timedelta(days=31)},
                {"job_id": new_job, "done": 1, "total": 2, "updated_at": now - timedelta(days=1)},
            ],
        )
        for read_at, ref in ((now - timedelta(days=91), "old"), (now - timedelta(days=1), "new")):
            connection.execute(
                notifications.insert().values(
                    user_id=user, kind="job_done", ref_kind="job", ref_id=ref, read_at=read_at
                )
            )
        connection.execute(
            notifications.insert().values(
                user_id=user,
                kind="job_done",
                ref_kind="job",
                ref_id="unread",
                created_at=now - timedelta(days=200),
            )
        )
    engine = make_engine(settings)
    try:
        prune.__operation__.run(Caller(sessions=make_sessions(engine), settings=settings))
    finally:
        engine.dispose()
    try:
        with owner_engine.connect() as connection:
            left = [
                connection.execute(
                    select(table.c.expires_at).where(table.c.user_id == user)
                ).scalars()
                for table in (sessions_table, tokens)
            ]
            assert [[at > now for at in rows] for rows in left] == [[True], [True]]
            kept = connection.execute(
                select(progress.c.job_id).where(progress.c.job_id.in_([old_job, new_job]))
            ).scalars()
            assert list(kept) == [new_job]
            refs = connection.execute(
                select(notifications.c.ref_id).where(notifications.c.user_id == user)
            ).scalars()
            assert sorted(refs) == ["new", "unread"]
    finally:
        with owner_engine.begin() as connection:
            connection.execute(progress.delete().where(progress.c.job_id == new_job))
            for table in (notifications, sessions_table, tokens):
                connection.execute(table.delete().where(table.c.user_id == user))
            connection.execute(users.delete().where(users.c.id == user))
