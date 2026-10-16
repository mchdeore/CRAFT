"""Resource scope is applied before retrieval filtering, scores and counts."""

from sqlalchemy import and_, exists, or_, true
from sqlalchemy.orm import Session

from app.infrastructure.access import Caller
from app.infrastructure.database.metadata import mission_grants, provider_items


def readable(caller: Caller):
    if "admin" in caller.roles:
        return true()
    return or_(
        provider_items.c.mission_key.is_(None),
        exists().where(
            and_(
                mission_grants.c.user_id == caller.user_id,
                mission_grants.c.mission_key == provider_items.c.mission_key,
                or_(
                    mission_grants.c.silo.is_(None), mission_grants.c.silo == provider_items.c.silo
                ),
            )
        ),
    )


def insert_grant(session: Session, user, mission: str, silo: str | None) -> None:
    from sqlalchemy.dialects.postgresql import insert

    session.execute(
        insert(mission_grants)
        .values(user_id=user, mission_key=mission, silo=silo)
        .on_conflict_do_nothing()
    )


def remove_grant(session: Session, user, mission: str, silo: str | None) -> None:
    session.execute(
        mission_grants.delete().where(
            mission_grants.c.user_id == user,
            mission_grants.c.mission_key == mission,
            mission_grants.c.silo == silo,
        )
    )
