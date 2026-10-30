"""Find, get, compare and similar missions read only what the caller's grants reach."""

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import msgspec
import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.cube.handlers import (
    CompareMissions,
    Condition,
    FindMissions,
    MissionRef,
    SimilarMissions,
    compare_missions,
    find_missions,
    get_mission,
    similar_missions,
)
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import NotFound
from app.library.convert import convert
from app.tests.support import rows
from app.tests.support.cube import cube_source as cube_source

BUDGET = b"# 1 Budget\n\n| Parameter | Value | Unit |\n|---|---|---|\n| Peak power | 350 | W |\n"


@pytest.fixture
def cube(cube_source):
    """cube_source with a second mission, power in W and kW, and callers with chosen grants.

    `value` and `facet` add rows to either mission, citing that mission's budget document.
    """
    source, owner, provider = cube_source, cube_source["owner"], cube_source["provider"]
    users, roles = m.metadata.tables["users"], m.metadata.tables["user_roles"]
    other = "msn-" + uuid4().hex
    with owner.begin() as connection:
        connection.execute(
            m.missions.insert().values(
                key=other, title="Second mission", current_stage="st3-design", simulated=True
            )
        )
        added = connection.execute(
            insert(m.unit_conversions)
            .values(
                [
                    {"parameter": "power_peak", "unit": "W", "multiplier": 1},
                    {"parameter": "power_peak", "unit": "kW", "multiplier": 1000},
                ]
            )
            .on_conflict_do_nothing()
            .returning(m.unit_conversions.c.unit)
        )
        units = added.scalars().all()
        synonym = connection.execute(
            insert(m.header_synonyms)
            .values(header="peak power", parameter="power_peak", language="en")
            .on_conflict_do_nothing()
            .returning(m.header_synonyms.c.header)
        ).first()
    provider.write_content(f"missions/{other}/engineering/st3-design/budget.md", BUDGET)
    rows.load(owner, provider)
    documents = {
        record["mission_key"]: key
        for key, record in provider.items().items()
        if record["path"].endswith("/budget.md")
    }
    members = []

    def member(*grants):
        """A member with these (mission, silo) grants; a silo of None reads every silo."""
        with owner.begin() as connection:
            user = connection.execute(
                users.insert()
                .values(username="cube_" + uuid4().hex, display_name="Generated member")
                .returning(users.c.id)
            ).scalar_one()
            connection.execute(roles.insert().values(user_id=user, role="member"))
            for mission, silo in grants:
                connection.execute(
                    m.mission_grants.insert().values(user_id=user, mission_key=mission, silo=silo)
                )
        members.append(user)
        return replace(source["caller"], user_id=user)

    def value(mission, silo, watts):
        item = documents[mission]
        path = provider.items()[item]["path"]
        block = next(b for b in convert(provider.read_content(item, 1), path) if b.kind == "table")
        with owner.begin() as connection:
            return connection.execute(
                m.cube_values.insert()
                .values(
                    mission_key=mission,
                    silo=silo,
                    stage="st3-design",
                    parameter="power_peak",
                    kind="exact",
                    low=watts,
                    high=watts,
                    valid_from=date(2026, 1, 1),
                    recorded_at=datetime(2026, 1, 10, tzinfo=UTC),
                    item=item,
                    version=1,
                    block=block.block,
                    locator=block.locator + ";row=1",
                    quote_sha256=hashlib.sha256(block.text.splitlines()[2].encode()).digest(),
                    simulated=True,
                )
                .returning(m.cube_values.c.id)
            ).scalar_one()

    def facet(mission, name, vector):
        with owner.begin() as connection:
            text = connection.execute(
                m.facet_texts.insert()
                .values(
                    mission_key=mission,
                    facet=name,
                    text="Generated " + name,
                    sources=[documents[mission]],
                    model="scripted",
                )
                .returning(m.facet_texts.c.id)
            ).scalar_one()
            connection.execute(
                m.vectors.insert().values(
                    owner_kind="facet_text",
                    owner_id=text,
                    model=source["settings"].model_embedding,
                    recipe="facet-v1",
                    embedding=vector + [0.0] * (1536 - len(vector)),
                )
            )

    yield source | {
        "other": other,
        "documents": documents,
        "member": member,
        "value": value,
        "facet": facet,
    }
    with owner.begin() as connection:
        texts = select(m.facet_texts.c.id).where(m.facet_texts.c.mission_key == other)
        connection.execute(
            m.vectors.delete().where(
                m.vectors.c.owner_kind == "facet_text", m.vectors.c.owner_id.in_(texts)
            )
        )
        connection.execute(m.facet_texts.delete().where(m.facet_texts.c.mission_key == other))
        connection.execute(m.cube_values.delete().where(m.cube_values.c.mission_key == other))
        connection.execute(m.missions.delete().where(m.missions.c.key == other))
        connection.execute(m.mission_grants.delete().where(m.mission_grants.c.user_id.in_(members)))
        connection.execute(roles.delete().where(roles.c.user_id.in_(members)))
        connection.execute(users.delete().where(users.c.id.in_(members)))
        if synonym:
            connection.execute(
                m.header_synonyms.delete().where(m.header_synonyms.c.header == "peak power")
            )
        connection.execute(
            m.unit_conversions.delete().where(
                m.unit_conversions.c.parameter == "power_peak", m.unit_conversions.c.unit.in_(units)
            )
        )


def above(watts):
    condition = Condition(parameter="power_peak", op="ge", value=str(watts), unit="W")
    return FindMissions(conditions=[condition])


def found(caller, request):
    result = find_missions(caller, request)
    return {row.mission.key: [value.id for value in row.matched] for row in result.missions}


def compared(caller, request):
    rows = compare_missions(caller, request).rows
    return [[value.id if value else None for value in row.values] for row in rows]


def test_a_silo_grant_limits_find_get_and_compare_to_that_silo(cube):
    a, b = cube["mission"], cube["other"]
    engineering_a = cube["value"](a, "engineering", 400)
    budget_a = cube["value"](a, "budget", 900)
    engineering_b = cube["value"](b, "engineering", 350)
    everyone = cube["member"]((a, None), (b, None))
    engineers = cube["member"]((a, "engineering"), (b, "engineering"))
    budget = cube["member"]((a, "budget"))

    assert found(everyone, above(800)) == {a: [budget_a]}
    assert found(engineers, above(800)) == {}
    assert found(engineers, above(300)) == {a: [engineering_a], b: [engineering_b]}
    assert found(budget, above(300)) == {a: [budget_a]}
    # A mission matched in two silos shows one: the first silo by name.
    assert found(everyone, above(300)) == {a: [budget_a], b: [engineering_b]}

    def values(caller, key):
        return [value.id for value in get_mission(caller, MissionRef(key=key)).values]

    assert sorted(values(everyone, a)) == sorted([engineering_a, budget_a])
    assert values(engineers, a) == [engineering_a]
    assert values(budget, a) == [budget_a]
    with pytest.raises(NotFound):
        get_mission(budget, MissionRef(key=b))

    both = CompareMissions(keys=[a, b], parameters=["power_peak"])
    assert compared(engineers, both) == [[engineering_a], [engineering_b]]
    # Two silos' totals for one mission aren't comparable until the scope picks a silo.
    assert compared(everyone, both) == [[None], [engineering_b]]
    engineering = msgspec.structs.replace(both, silo="engineering")
    assert compared(everyone, engineering) == [[engineering_a], [engineering_b]]
    with pytest.raises(NotFound):
        compare_missions(budget, both)


def test_similar_missions_score_readable_facet_evidence(cube):
    a, b = cube["mission"], cube["other"]
    reader = cube["member"]((a, None), (b, None))
    cube["facet"](a, "design", [1.0])
    cube["facet"](a, "operations", [0.0, 1.0])
    cube["facet"](b, "design", [1.0])
    cube["facet"](b, "operations", [0.0, 1.0, 1.0])
    two = SimilarMissions(key=a, facets=["design", "operations"])

    [match] = similar_missions(reader, two).missions
    assert (match.mission.key, match.comparable) == (b, True)
    assert match.facet_scores == pytest.approx({"design": 1.0, "operations": 0.5**0.5})
    assert match.score == pytest.approx((1 + 0.5**0.5) / 2)

    # Two shared facets out of five are too few to compare.
    [match] = similar_missions(reader, SimilarMissions(key=a)).missions
    assert (match.comparable, match.score) == (False, None)

    # Evidence whose source was withdrawn no longer counts.
    cube["provider"].delete_item(cube["documents"][b])
    [match] = similar_missions(reader, two).missions
    assert (match.comparable, match.score) == (False, None)
    assert match.facet_scores == {"design": None, "operations": None}

    with pytest.raises(NotFound):
        similar_missions(reader, SimilarMissions(key="msn-unknown"))
