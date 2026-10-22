"""Comparison does not merge incompatible elements and evidence preserves source versions."""

from datetime import date

import pytest
from sqlalchemy import select

from app.cube.handlers import compare_missions, get_value_evidence
from app.cube.tests.test_parametric import demo_caller, install_units
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import InvalidInput, NotFound
from app.library.handlers import index_provider


def test_compare_scope(demo):
    caller, app = demo_caller(demo)
    try:
        for count in [1, 6]:
            with pytest.raises(InvalidInput):
                compare_missions(
                    caller,
                    {
                        "keys": [f"msn-sim-{n:02}" for n in range(1, count + 1)],
                        "parameters": ["mass_dry"],
                    },
                )
        result = compare_missions(
            caller,
            {"keys": ["msn-sim-01", "msn-sim-02"], "parameters": ["mass_dry"], "stage": "current"},
        )
        assert len(result.rows) == 2 and all(row.values[0] is not None for row in result.rows)
        assert all(row.values[0].element is None for row in result.rows if row.values[0])
        # Replace totals with different source-backed components in this owned database.
        with demo["sessions"].begin() as session:
            for key, name in [
                ("msn-sim-01", "power subsystem"),
                ("msn-sim-02", "structure"),
            ]:
                element = session.execute(
                    select(m.elements.c.id).where(
                        m.elements.c.mission_key == key, m.elements.c.name == name
                    )
                ).scalar_one()
                session.execute(
                    m.cube_values.update()
                    .where(
                        m.cube_values.c.mission_key == key, m.cube_values.c.parameter == "mass_dry"
                    )
                    .values(element_id=element)
                )
        result = compare_missions(
            caller,
            {"keys": ["msn-sim-01", "msn-sim-02"], "parameters": ["mass_dry"], "stage": "current"},
        )
        assert all(row.values[0] is None for row in result.rows)
    finally:
        app.state.engine.dispose()


@pytest.mark.parametrize("indexed", [False, True], ids=["unindexed", "indexed"])
def test_evidence_status(cube_source, indexed):
    install_units(cube_source)
    identifier = cube_source["add"](source_number="0.4", source_unit="kW")
    request = {"id": str(identifier)}
    initial = get_value_evidence(cube_source["caller"], request)
    matched = initial.citation_status == "current" and initial.conversion == "0.4 kW × 1000 = 400 W"
    assert matched and initial.quote
    assert initial.value.kind == "exact" and initial.value.low == initial.value.high == 400
    assert not initial.value.withdrawn and "400" in initial.quote
    original = cube_source["provider"].read_content(cube_source["item"], 1)
    cube_source["provider"].write_content(cube_source["path"], original.replace(b"400", b"450"))
    index_provider(cube_source["settings"])
    newer = get_value_evidence(cube_source["caller"], request)
    equal = newer.quote == initial.quote
    assert newer.citation_status == "newer_version" and equal
    assert newer.conversion == "0.4 kW × 1000 = 400 W"
    assert not newer.value.withdrawn and newer.value.low == newer.value.high == 400
    assert "400" in newer.quote and "450" not in newer.quote
    cube_source["add"](450, valid=date(2026, 3, 1), version=2)
    cube_source["provider"].delete_item(cube_source["item"])
    if indexed:
        index_provider(cube_source["settings"])
    withdrawn = get_value_evidence(cube_source["caller"], request)
    assert withdrawn.citation_status == "withdrawn" and withdrawn.value.withdrawn
    assert withdrawn.value.kind == "unknown"
    assert withdrawn.value.low is None and withdrawn.value.high is None
    assert withdrawn.quote == ""
    assert withdrawn.conversion == "No numeric source value."


@pytest.mark.parametrize(
    "change,indexed",
    [
        ("grant", False),
        ("moved", False),
        ("moved", True),
        ("moved_withdrawn", False),
        ("moved_withdrawn", True),
    ],
)
def test_evidence_current_access_refusal_has_no_quote(cube_source, change, indexed):
    source = cube_source
    identifier = source["add"]()
    request = {"id": str(identifier)}
    with source["owner"].begin() as connection:
        connection.execute(
            m.mission_grants.update()
            .where(
                m.mission_grants.c.user_id == source["caller"].user_id,
                m.mission_grants.c.mission_key == source["mission"],
            )
            .values(silo="engineering")
        )
    initial = get_value_evidence(source["caller"], request)
    assert initial.citation_status == "current" and "400" in initial.quote
    if change == "grant":
        with source["owner"].begin() as connection:
            connection.execute(
                m.mission_grants.delete().where(
                    m.mission_grants.c.user_id == source["caller"].user_id
                )
            )
    else:
        destination = source["provider"].path(source["path"].replace("/engineering/", "/budget/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        source["provider"].path(source["path"]).rename(destination)
        source["provider"].scan()
        if change == "moved_withdrawn":
            source["provider"].delete_item(source["item"])
        if indexed:
            index_provider(source["settings"])
    with pytest.raises(NotFound) as denied:
        get_value_evidence(source["caller"], request)
    assert denied.value.status == 404


@pytest.mark.slow
def test_cube_contract():
    import json
    from pathlib import Path

    import jsonschema
    import msgspec

    from app.cube.handlers import Comparison, FoundMissions, MissionState, Parameter, ValueEvidence
    from app.tests.test_contract import root_schema, typescript_schema

    for shape, name in [
        (Parameter, "parameters"),
        (MissionState, "mission-state"),
        (FoundMissions, "found-missions"),
        (Comparison, "comparison"),
        (ValueEvidence, "value-evidence"),
    ]:
        data = json.loads(Path("app/contract/examples/" + name + ".json").read_text())
        compiled = typescript_schema(shape.__name__)
        declared = root_schema(compiled)
        python = root_schema(msgspec.json.schema(shape))
        assert set(declared["properties"]) == set(python["properties"])
        assert set(declared["required"]) == set(python["required"])
        for item in data if isinstance(data, list) else [data]:
            msgspec.convert(item, shape)
            jsonschema.validate(item, compiled)
