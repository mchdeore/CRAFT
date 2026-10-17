"""Cube queries receive a session; transaction decisions stay with the service."""

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.cube import state
from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.database.metadata import (
    mission_aliases,
    mission_grants,
    missions,
    stages,
)
from app.infrastructure.errors import InvalidInput


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


def similarity_missions(session: Session, caller: Caller):
    rows = [
        dict(row)
        for row in session.execute(
            select(missions).where(mission_access(caller)).order_by(missions.c.key)
        ).mappings()
    ]
    for row in rows:
        row["aliases"] = list(
            session.execute(
                select(mission_aliases.c.alias)
                .where(mission_aliases.c.mission_key == row["key"])
                .order_by(mission_aliases.c.alias)
            ).scalars()
        )
    return rows


def dictionary(session: Session) -> dict:
    rows = {
        row["key"]: dict(row) | {"conversions": {}}
        for row in session.execute(select(m.parameters)).mappings()
    }
    for unit in session.execute(
        select(m.unit_conversions).order_by(m.unit_conversions.c.unit)
    ).mappings():
        rows[unit["parameter"]]["conversions"][unit["unit"]] = (
            unit["multiplier"],
            unit["value_offset"],
        )
    return rows


def intake_dictionary(session: Session, caller: Caller, target, silo):
    synonyms = {}
    for row in session.execute(select(m.header_synonyms)).mappings():
        synonyms.setdefault(row["header"], []).append(row["parameter"])
    names = {}
    if target:
        query = select(m.elements.c.name).where(
            m.elements.c.mission_key == target,
            exists().where(
                mission_grants.c.user_id == caller.user_id,
                mission_grants.c.mission_key == m.elements.c.mission_key,
                or_(mission_grants.c.silo.is_(None), mission_grants.c.silo == m.elements.c.silo),
            ),
        )
        if silo:
            query = query.where(m.elements.c.silo == silo)
        names = {name.casefold(): name for name in session.execute(query).scalars()}
    return dictionary(session), synonyms, names


def intake_grant(session: Session, caller: Caller, target, silo) -> bool:
    query = select(mission_grants.c.user_id).where(
        mission_grants.c.user_id == caller.user_id,
        mission_grants.c.mission_key == target,
    )
    if silo:
        query = query.where(or_(mission_grants.c.silo.is_(None), mission_grants.c.silo == silo))
    return session.execute(query.limit(1)).first() is not None


def publication_elements(session: Session, mission, silo):
    return {
        row["name"]: row["id"]
        for row in session.execute(
            select(m.elements.c.name, m.elements.c.id).where(
                m.elements.c.mission_key == mission, m.elements.c.silo == silo
            )
        ).mappings()
    }


def append_values(session: Session, rows):
    return list(
        session.execute(m.cube_values.insert().returning(m.cube_values.c.id), rows).scalars()
    )


def scope(session: Session, silo, stage) -> None:
    if (
        silo is not None
        and not session.execute(select(m.silos.c.key).where(m.silos.c.key == silo)).first()
    ):
        raise InvalidInput()
    if (
        stage is not None
        and stage != "current"
        and not session.execute(select(stages.c.key).where(stages.c.key == stage)).first()
    ):
        raise InvalidInput()


def mission(session: Session, caller: Caller, key: str):
    match = or_(
        func.lower(missions.c.key) == key.lower(),
        exists().where(
            mission_aliases.c.mission_key == missions.c.key, mission_aliases.c.alias == key.lower()
        ),
    )
    row = session.execute(select(missions).where(mission_access(caller), match)).mappings().first()
    if row is None:
        return None
    return dict(row) | {
        "aliases": list(
            session.execute(
                select(mission_aliases.c.alias)
                .where(mission_aliases.c.mission_key == row["key"])
                .order_by(mission_aliases.c.alias)
            ).scalars()
        )
    }


def value(session: Session, caller: Caller, identifier):
    row = (
        session.execute(
            select(m.cube_values).where(
                m.cube_values.c.id == identifier, state.readable_values(caller)
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def matching_groups(picked, conditions, active_items):
    groups = (
        select(picked.c.mission_key, picked.c.silo, picked.c.stage, picked.c.element_id)
        .distinct()
        .cte("groups")
    )
    clauses = []
    for number, (parameter, op, low, high) in enumerate(conditions):
        row = picked.alias("condition_" + str(number))
        numeric = (
            (row.c.low == low) & (row.c.high == low)
            if op == "eq"
            else row.c.low >= low
            if op == "ge"
            else row.c.high <= low
            if op == "le"
            else (row.c.low >= low) & (row.c.high <= high)
        )
        clauses.append(
            exists(
                select(row.c.id).where(
                    row.c.mission_key == groups.c.mission_key,
                    row.c.silo == groups.c.silo,
                    row.c.stage.is_not_distinct_from(groups.c.stage),
                    row.c.element_id.is_not_distinct_from(groups.c.element_id),
                    row.c.parameter == parameter,
                    row.c.kind.in_(["exact", "range"]),
                    row.c.item.in_(active_items),
                    numeric,
                )
            )
        )
    return select(groups).where(*clauses).cte("matching_groups")


def find(session: Session, caller: Caller, at, known_at, scope, conditions, active_items, limit):
    chosen = state.picked(caller, at, known_at, **scope)
    groups = matching_groups(chosen, conditions, active_items)
    allowed = exists(select(groups.c.mission_key).where(groups.c.mission_key == missions.c.key))
    rows = (
        session.execute(
            select(missions)
            .where(mission_access(caller), allowed)
            .order_by(missions.c.created_at.desc(), missions.c.key)
            .limit(limit + 1)
        )
        .mappings()
        .all()
    )
    result = []
    for row in rows:
        group = (
            session.execute(
                select(groups)
                .where(groups.c.mission_key == row["key"])
                .order_by(groups.c.element_id.asc().nulls_first(), groups.c.silo, groups.c.stage)
            )
            .mappings()
            .first()
        )
        assert group is not None
        values = (
            session.execute(
                select(chosen).where(
                    chosen.c.mission_key == row["key"],
                    chosen.c.silo == group["silo"],
                    chosen.c.stage.is_not_distinct_from(group["stage"]),
                    chosen.c.element_id.is_not_distinct_from(group["element_id"]),
                    chosen.c.parameter.in_([c[0] for c in conditions]),
                )
            )
            .mappings()
            .all()
        )
        result.append((mission(session, caller, row["key"]), [dict(value) for value in values]))
    return result
