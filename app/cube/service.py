"""Readable mission lists and short owner-selected maintenance transactions."""

import msgspec

from app.cube import storage
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import NotFound


def list_missions(caller, request):
    from app.cube.handlers import Mission

    if caller.sessions is None:
        raise NotFound()
    with unit_of_work(caller.sessions, read_only=True) as session:
        rows = storage.list_missions(session, caller, request.query, request.limit)
    return [
        msgspec.convert(
            {
                "key": row["key"],
                "title": row["title"],
                "currentStage": row["current_stage"],
                "aliases": row["aliases"],
                "simulated": row["simulated"],
            },
            Mission,
        )
        for row in rows
    ]


def change_stage(settings, mission: str, stage: str) -> None:
    sessions, engine = make_owner_sessions(settings)
    try:
        with unit_of_work(sessions) as session:
            if not storage.set_stage(session, mission, stage):
                raise NotFound()
    finally:
        engine.dispose()
