"""Cube reference rows, constraints, exact grants and transport access."""

import json
from datetime import date
from uuid import uuid4

import jsonschema
import msgspec
import pytest
from alembic import command
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError

from app.cli import main
from app.cube.handlers import ListMissions, Mission, list_missions
from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import NotFound
from app.infrastructure.mcp.stdio import local_caller
from app.infrastructure.tests.test_grants import assert_grants
from app.infrastructure.tests.test_migrations import isolated_migrations as isolated_migrations
from app.seed.base import seed
from app.tests.test_contract import root_schema, typescript_schema

TABLES = {
    "silos",
    "stages",
    "missions",
    "mission_aliases",
    "elements",
    "parameters",
    "unit_conversions",
    "header_synonyms",
    "cube_values",
    "facet_texts",
}


@pytest.fixture
def cube(owner_engine):
    mission = "msn-" + uuid4().hex
    parameter = "p_" + uuid4().hex
    with owner_engine.begin() as connection:
        connection.execute(
            m.missions.insert().values(
                key=mission, title="Synthetic mission", current_stage="st3-design", simulated=True
            )
        )
        connection.execute(
            m.parameters.insert().values(key=parameter, label="Synthetic", unit="kg")
        )
    yield {
        "mission_key": mission,
        "silo": "engineering",
        "parameter": parameter,
        "kind": "exact",
        "low": 1,
        "high": 1,
        "valid_from": date(2026, 1, 1),
        "item": "itm_000000000001",
        "version": 1,
        "block": 1,
        "locator": "heading=1;block=1",
        "quote_sha256": bytes(32),
        "simulated": True,
    }
    with owner_engine.begin() as connection:
        connection.execute(m.cube_values.delete().where(m.cube_values.c.parameter == parameter))
        connection.execute(m.facet_texts.delete().where(m.facet_texts.c.mission_key == mission))
        connection.execute(m.parameters.delete().where(m.parameters.c.key == parameter))
        connection.execute(m.missions.delete().where(m.missions.c.key == mission))


def test_reference_lists(owner_engine) -> None:
    with owner_engine.connect() as connection:
        assert connection.execute(
            select(m.silos.c.key).order_by(m.silos.c.seq)
        ).scalars().all() == [
            "requirements",
            "quality",
            "launch",
            "engineering",
            "reviews",
            "budget",
        ]
        stages = connection.execute(
            select(m.stages.c.key, m.stages.c.gate).order_by(m.stages.c.seq)
        ).all()
    assert [row.key for row in stages] == [
        "st1-concept",
        "st2-definition",
        "st3-design",
        "st4-build",
        "st5-verification",
        "st6-operation",
        "st7-closure",
    ]
    assert [row.gate for row in stages] == [
        "The mission need and options are agreed",
        "System requirements are baselined",
        "The design review passes",
        "Units are built and integrated",
        "The test campaign is complete",
        "The mission ends",
        "Disposal and lessons are recorded",
    ]


def test_schema_nullability_defaults(owner_engine) -> None:
    nullable = {
        "elements": {"parent_id"},
        "parameters": {"min_value", "max_value"},
        "cube_values": {
            "stage",
            "element_id",
            "low",
            "high",
            "source_number",
            "source_unit",
            "approval_request_id",
        },
    }
    inspector = inspect(owner_engine)
    for name in TABLES:
        for column in inspector.get_columns(name):
            assert column["nullable"] == (column["name"] in nullable.get(name, set()))


def test_cube_grants(app, owner_engine, cube) -> None:
    assert_grants(
        owner_engine,
        {name: {"SELECT", "INSERT"} if name == "cube_values" else {"SELECT"} for name in TABLES},
    )
    with app.state.engine.connect() as connection, connection.begin() as transaction:
        for name in TABLES:
            connection.execute(select(m.metadata.tables[name]).limit(1)).all()
        identifier = connection.execute(
            m.cube_values.insert().values(**cube).returning(m.cube_values.c.id)
        ).scalar_one()
        for statement in [
            m.cube_values.update().where(m.cube_values.c.id == identifier).values(low=2),
            m.cube_values.delete().where(m.cube_values.c.id == identifier),
            m.missions.insert().values(
                key="msn-denied", title="Synthetic", current_stage="st3-design", simulated=True
            ),
        ]:
            with pytest.raises(DBAPIError, match="permission denied"), connection.begin_nested():
                connection.execute(statement)
        transaction.rollback()


def test_value_checks(owner_engine, cube) -> None:
    with owner_engine.connect() as connection, connection.begin() as transaction:
        for updates in [
            {"kind": "exact", "low": 1, "high": 2},
            {"kind": "range", "low": 1, "high": None},
            {"kind": "range", "low": 3, "high": 2},
            {"kind": "unknown", "low": 1, "high": None},
            {"kind": "unknown", "low": None, "high": 1},
            {"kind": "unknown", "low": 1, "high": 1},
            {"kind": None},
            {"quote_sha256": bytes(31)},
        ]:
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(m.cube_values.insert().values(**(cube | updates)))
        for kind in ["unknown", "not_applicable"]:
            connection.execute(
                m.cube_values.insert().values(**(cube | {"kind": kind, "low": None, "high": None}))
            )
        transaction.rollback()


def test_mission_key_and_stage(owner_engine) -> None:
    with owner_engine.connect() as connection, connection.begin() as transaction:
        for key, stage in [("bad-key", "st3-design"), ("msn-synthetic", "missing-stage")]:
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    m.missions.insert().values(
                        key=key, title="Synthetic", current_stage=stage, simulated=True
                    )
                )
        transaction.rollback()


def test_missions_stage_cli(settings, owner_engine, monkeypatch, tmp_path) -> None:
    seed(msgspec.structs.replace(settings, provider_root=str(tmp_path)))
    monkeypatch.setenv("CRAFT_DATABASE_NAME", "craft_test")
    with owner_engine.connect() as connection:
        original = connection.execute(
            select(m.missions.c.current_stage).where(m.missions.c.key == "msn-sim-01")
        ).scalar_one()
    try:
        main(["missions", "stage", "msn-sim-01", "st4-build"])
        with pytest.raises(NotFound):
            main(["missions", "stage", "msn-sim-01", "missing-stage"])
        with owner_engine.connect() as connection:
            assert (
                connection.execute(
                    select(m.missions.c.current_stage).where(m.missions.c.key == "msn-sim-01")
                ).scalar_one()
                == "st4-build"
            )
    finally:
        with owner_engine.begin() as connection:
            connection.execute(
                m.missions.update()
                .where(m.missions.c.key == "msn-sim-01")
                .values(current_stage=original)
            )


def test_list_missions_access(settings, tmp_path) -> None:
    seed(msgspec.structs.replace(settings, provider_root=str(tmp_path)))
    demo = local_caller("demo", settings)
    restricted = local_caller("restricted", settings)
    result = list_missions(demo, ListMissions())
    assert {"msn-sim-01", "msn-sim-02"} <= {row.key for row in result}
    assert list_missions(restricted, ListMissions()) == []
    alias = list_missions(demo, ListMissions(query="SIM-02"))
    assert [row.key for row in alias] == ["msn-sim-02"]
    assert len(list_missions(demo, ListMissions(limit=1))) == 1
    schema = typescript_schema("Mission")
    for row in result:
        jsonschema.validate(msgspec.to_builtins(row), schema)
    declared = root_schema(schema)
    python = root_schema(msgspec.json.schema(Mission))
    assert set(declared["properties"]) == set(python["properties"])
    assert set(declared["required"]) == set(python["required"])
    example = json.load(open("api/examples/missions.json"))
    for row in example:
        jsonschema.validate(row, schema)


def test_facet_check(owner_engine, cube) -> None:
    values = {
        "mission_key": cube["mission_key"],
        "facet": "design",
        "text": "Synthetic facet",
        "sources": ["itm_000000000001"],
        "model": "scripted",
    }
    with owner_engine.connect() as connection, connection.begin() as transaction:
        connection.execute(m.facet_texts.insert().values(**values))
        for update in [{}, {"facet": "unknown"}, {"facet": None}]:
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(m.facet_texts.insert().values(**(values | update)))
        transaction.rollback()


def test_cube_admin_requires_mission_grants(owner_engine, app, cube) -> None:
    with owner_engine.begin() as connection:
        user = connection.execute(
            m.metadata.tables["users"]
            .insert()
            .values(username="cube_admin_" + uuid4().hex, display_name="Synthetic admin")
            .returning(m.metadata.tables["users"].c.id)
        ).scalar_one()
        connection.execute(
            m.metadata.tables["user_roles"].insert().values(user_id=user, role="admin")
        )
    caller = Caller(
        user_id=user,
        roles=frozenset({"admin"}),
        sessions=app.state.sessions,
        settings=app.state.settings,
    )
    try:
        assert list_missions(caller, ListMissions()) == []
        with owner_engine.begin() as connection:
            connection.execute(
                m.mission_grants.insert().values(
                    user_id=user, mission_key=cube["mission_key"], silo="engineering"
                )
            )
        assert [row.key for row in list_missions(caller, ListMissions())] == [cube["mission_key"]]
    finally:
        with owner_engine.begin() as connection:
            connection.execute(m.mission_grants.delete().where(m.mission_grants.c.user_id == user))
            connection.execute(
                m.metadata.tables["user_roles"]
                .delete()
                .where(m.metadata.tables["user_roles"].c.user_id == user)
            )
            connection.execute(
                m.metadata.tables["users"].delete().where(m.metadata.tables["users"].c.id == user)
            )


def test_migration_round_trip(isolated_migrations) -> None:
    config, connection = isolated_migrations
    command.downgrade(config, "0008")
    assert not TABLES.intersection(inspect(connection).get_table_names())
    command.upgrade(config, "head")
    assert TABLES <= set(inspect(connection).get_table_names())
    assert connection.execute(text("select count(*) from stages")).scalar_one() == 7
