"""Select one accepted record in each logical cell before inspecting its value."""

from datetime import date, datetime

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.provider import get_provider


def readable_values(caller: Caller):
    return exists().where(
        m.mission_grants.c.user_id == caller.user_id,
        m.mission_grants.c.mission_key == m.cube_values.c.mission_key,
        or_(m.mission_grants.c.silo.is_(None), m.mission_grants.c.silo == m.cube_values.c.silo),
    )


def picked(caller: Caller, at: date, known_at: datetime, *, silo=None, stage=None, keys=None):
    values = m.cube_values
    order = [values.c.valid_from.desc(), values.c.recorded_at.desc(), values.c.id.desc()]
    cells = [
        values.c.mission_key,
        values.c.silo,
        values.c.stage,
        values.c.element_id,
        values.c.parameter,
    ]
    query = select(
        values,
        m.missions.c.current_stage,
        func.row_number().over(partition_by=cells, order_by=order).label("position"),
    )
    query = query.join(m.missions, m.missions.c.key == values.c.mission_key).where(
        readable_values(caller), values.c.valid_from <= at, values.c.recorded_at <= known_at
    )
    if silo is not None:
        query = query.where(values.c.silo == silo)
    if stage == "current":
        query = query.where(values.c.stage == m.missions.c.current_stage)
    elif stage is not None:
        query = query.where(values.c.stage == stage)
    if keys is not None:
        query = query.where(values.c.mission_key.in_(keys))
    ranked = query.cte("ranked_cells")
    return select(ranked).where(ranked.c.position == 1).cte("picked_cells")


def latest(session: Session, caller: Caller, at: date, known_at: datetime, **scope) -> list[dict]:
    rows = picked(caller, at, known_at, **scope)
    return [dict(row) for row in session.execute(select(rows)).mappings()]


def with_sources(rows: list[dict], items: dict) -> list[dict]:
    result = []
    for row in rows:
        item = items.get(row["item"])
        withdrawn = item is None or item["deleted_at"] is not None
        result.append(
            row
            | ({"kind": "unknown", "low": None, "high": None} if withdrawn else {})
            | {"withdrawn": withdrawn}
        )
    return result


def sources(caller: Caller) -> dict:
    if caller.settings is None:
        return {}
    return get_provider(caller.settings.provider_root).items()
