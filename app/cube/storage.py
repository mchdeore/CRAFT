"""Cube queries receive a session; transaction decisions stay with the service."""

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.infrastructure.access import Caller
from app.infrastructure.database.metadata import (
    mission_aliases,
    mission_grants,
    missions,
    stages,
)


def mission_access(caller: Caller):
    return exists().where(
        mission_grants.c.user_id == caller.user_id, mission_grants.c.mission_key == missions.c.key
    )


def list_missions(session: Session, caller: Caller, query: str, limit: int):
    matches = or_(
        func.lower(missions.c.key).contains(query.lower(), autoescape=True),
        func.lower(missions.c.title).contains(query.lower(), autoescape=True),
        exists().where(
            mission_aliases.c.mission_key == missions.c.key,
            func.lower(mission_aliases.c.alias).contains(query.lower(), autoescape=True),
        ),
    )
    rows = (
        session.execute(
            select(missions)
            .where(mission_access(caller), matches)
            .order_by(missions.c.created_at.desc(), missions.c.key)
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [
        dict(row)
        | {
            "aliases": session.execute(
                select(mission_aliases.c.alias)
                .where(mission_aliases.c.mission_key == row["key"])
                .order_by(mission_aliases.c.alias)
            )
            .scalars()
            .all()
        }
        for row in rows
    ]


def set_stage(session: Session, mission: str, stage: str) -> bool:
    if session.execute(select(stages.c.key).where(stages.c.key == stage)).first() is None:
        return False
    return (
        session.execute(
            missions.update()
            .where(missions.c.key == mission)
            .values(current_stage=stage)
            .returning(missions.c.key)
        ).first()
        is not None
    )
