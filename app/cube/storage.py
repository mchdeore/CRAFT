"""Cube queries receive a session; transaction decisions stay with the service."""

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.cube import state
from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.database.metadata import mission_aliases, missions, stages
from app.infrastructure.errors import InvalidInput
from app.infrastructure.grants import cube_readable, mission_listed


def with_aliases(session: Session, rows) -> list[dict]:
    """Mission rows as dicts, each with its sorted aliases, read in one query."""
    rows = [dict(row) for row in rows]
    aliases = {row["key"]: [] for row in rows}
    if aliases:
        for key, alias in session.execute(
            select(mission_aliases.c.mission_key, mission_aliases.c.alias)
            .where(mission_aliases.c.mission_key.in_(list(aliases)))
            .order_by(mission_aliases.c.mission_key, mission_aliases.c.alias)
        ):
            aliases[key].append(alias)
    return [row | {"aliases": aliases[row["key"]]} for row in rows]


def list_missions(session: Session, caller: Caller, query: str, limit: int):
    matches = or_(
        func.lower(missions.c.key).contains(query.lower(), autoescape=True),
        func.lower(missions.c.title).contains(query.lower(), autoescape=True),
        exists().where(
            mission_aliases.c.mission_key == missions.c.key,
            func.lower(mission_aliases.c.alias).contains(query.lower(), autoescape=True),
        ),
    )
    rows = session.execute(
        select(missions)
        .where(mission_listed(caller, missions.c.key), matches)
        .order_by(missions.c.created_at.desc(), missions.c.key)
        .limit(limit)
    ).mappings()
    return with_aliases(session, rows)


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
    rows = session.execute(
        select(missions).where(mission_listed(caller, missions.c.key)).order_by(missions.c.key)
    ).mappings()
    return with_aliases(session, rows)


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
            cube_readable(caller, m.elements.c.mission_key, m.elements.c.silo),
        )
        if silo:
            query = query.where(m.elements.c.silo == silo)
        names = {name.casefold(): name for name in session.execute(query).scalars()}
    return dictionary(session), synonyms, names


def intake_grant(session: Session, caller: Caller, target, silo) -> bool:
    """With a silo, the cube rule for that silo; without one, the mission listing."""
    rule = cube_readable(caller, target, silo) if silo else mission_listed(caller, target)
    return session.execute(select(rule)).scalar_one()


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
    row = (
        session.execute(select(missions).where(mission_listed(caller, missions.c.key), match))
        .mappings()
        .first()
    )
    return None if row is None else with_aliases(session, [row])[0]


def value(session: Session, caller: Caller, identifier):
    row = (
        session.execute(
            select(m.cube_values).where(
                m.cube_values.c.id == identifier,
                cube_readable(caller, m.cube_values.c.mission_key, m.cube_values.c.silo),
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def value_labels(session: Session, caller: Caller, identifiers):
    """The readable values among these IDs, each with its parameter's label."""
    return (
        session.execute(
            select(m.cube_values.c.id, m.parameters.c.label)
            .join(m.parameters, m.parameters.c.key == m.cube_values.c.parameter)
            .where(
                m.cube_values.c.id.in_(list(identifiers)),
                cube_readable(caller, m.cube_values.c.mission_key, m.cube_values.c.silo),
            )
            .order_by(m.cube_values.c.id)
        )
        .mappings()
        .all()
    )


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
    rows = session.execute(
        select(missions)
        .where(mission_listed(caller, missions.c.key), allowed)
        .order_by(missions.c.created_at.desc(), missions.c.key)
        .limit(limit + 1)
    ).mappings()
    found = with_aliases(session, rows)
    if not found:
        return []
    # Each mission shows one group: its total before any element, then the first silo and stage.
    first = (
        select(groups)
        .distinct(groups.c.mission_key)
        .order_by(
            groups.c.mission_key,
            groups.c.element_id.asc().nulls_first(),
            groups.c.silo,
            groups.c.stage,
        )
        .cte("first_groups")
    )
    shown = and_(
        chosen.c.mission_key == first.c.mission_key,
        chosen.c.silo == first.c.silo,
        chosen.c.stage.is_not_distinct_from(first.c.stage),
        chosen.c.element_id.is_not_distinct_from(first.c.element_id),
    )
    values = {row["key"]: [] for row in found}
    for value in session.execute(
        select(chosen)
        .join(first, shown)
        .where(
            chosen.c.mission_key.in_(list(values)),
            chosen.c.parameter.in_([c[0] for c in conditions]),
        )
    ).mappings():
        values[value["mission_key"]].append(dict(value))
    return [(row, values[row["key"]]) for row in found]
