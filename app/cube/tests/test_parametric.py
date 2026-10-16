"""State selection precedes conditions and source withdrawal has no fallback."""

from datetime import UTC, date, datetime

from app.cube import state
from app.library.handlers import index_provider


def read_state(source, at, known=datetime(2026, 10, 1, tzinfo=UTC)):
    caller = source["caller"]
    with caller.sessions.begin() as session:
        rows = state.latest(session, caller, at, known, keys=[source["mission"]])
    return state.with_sources(rows, state.sources(caller))


def test_state_example(cube_source) -> None:
    source = cube_source
    source["add"]()
    original = source["provider"].read_content(source["item"], 1)
    source["provider"].write_content(source["path"], original.replace(b"400", b"450"))
    index_provider(source["settings"])
    source["add"](450, valid=date(2026, 3, 1), version=2)
    before = read_state(source, date(2026, 2, 15))[0]
    after = read_state(source, date(2026, 3, 15))[0]
    matched = before["low"] == 400 and after["low"] == 450
    assert matched
    source["provider"].delete_item(source["item"])
    withdrawn = read_state(source, date(2026, 3, 15))[0]
    matched = withdrawn["kind"] == "unknown" and withdrawn["withdrawn"] and withdrawn["low"] is None
    assert matched


def test_known_at(cube_source) -> None:
    cube_source["add"](400, recorded=datetime(2026, 1, 10, tzinfo=UTC))
    cube_source["add"](450, recorded=datetime(2026, 2, 10, tzinfo=UTC))
    row = read_state(cube_source, date(2026, 3, 1), datetime(2026, 2, 1, tzinfo=UTC))[0]
    matched = row["low"] == 400
    assert matched


def test_tie_order(cube_source) -> None:
    cube_source["add"](400, recorded=datetime(2026, 1, 10, tzinfo=UTC))
    chosen = cube_source["add"](450, recorded=datetime(2026, 1, 11, tzinfo=UTC))
    assert read_state(cube_source, date(2026, 3, 1))[0]["id"] == chosen


def filter_source(source, value="450", op="le", **fields):
    from app.cube.handlers import find_missions

    return find_missions(
        source["caller"],
        {
            "conditions": [
                {"parameter": "power_peak", "op": op, "value": value, "unit": "W", **fields}
            ],
            "at": "2026-03-15",
        },
    )


def install_units(source):
    from sqlalchemy.dialects.postgresql import insert

    from app.infrastructure.database import metadata as m

    with source["owner"].begin() as connection:
        for unit, multiplier in [("W", 1), ("kW", 1000)]:
            connection.execute(
                insert(m.unit_conversions)
                .values(parameter="power_peak", unit=unit, multiplier=multiplier, value_offset=0)
                .on_conflict_do_nothing()
            )


def test_unknown_replaces_number(cube_source):
    install_units(cube_source)
    cube_source["add"]()
    cube_source["add"](kind="unknown", valid=date(2026, 3, 1))
    assert not filter_source(cube_source).missions


def test_conversion_and_units(cube_source):
    import pytest

    from app.cube.handlers import find_missions, list_parameters
    from app.infrastructure.errors import InvalidInput

    install_units(cube_source)
    cube_source["add"](450)
    result = find_missions(
        cube_source["caller"],
        {"conditions": [{"parameter": "power_peak", "op": "eq", "value": "0.45", "unit": "kW"}]},
    )
    matched = len(result.missions) == 1 and result.missions[0].matched[0].low == 450
    assert matched
    assert any(p.key == "power_peak" for p in list_parameters(cube_source["caller"]))
    for value, unit in [
        ("450", ""),
        ("450", "MW"),
        ("1e3", "W"),
        ("NaN", "W"),
        ("Infinity", "W"),
        ("1" * 101, "W"),
    ]:
        with pytest.raises(InvalidInput):
            filter_source(cube_source, value=value, unit=unit)


def test_range_rule(cube_source):
    install_units(cube_source)
    cube_source["add"](400, high=450, kind="range")
    assert not filter_source(cube_source, "425").missions
    assert len(filter_source(cube_source, "450").missions) == 1
    assert not filter_source(cube_source, "425", "ge").missions
    assert len(filter_source(cube_source, "400", "between", high="450").missions) == 1
    assert not filter_source(cube_source, "410", "between", high="460").missions
    assert not filter_source(cube_source, "400", "eq").missions


def test_current_stage_filter(cube_source):
    from app.cube.handlers import get_mission
    from app.infrastructure.database import metadata as m

    cube_source["add"]()
    request = {"key": cube_source["mission"], "stage": "current"}
    assert len(get_mission(cube_source["caller"], request).values) == 1
    with cube_source["owner"].begin() as connection:
        connection.execute(
            m.missions.update()
            .where(m.missions.c.key == cube_source["mission"])
            .values(current_stage="st4-build")
        )
    assert not get_mission(cube_source["caller"], request).values


def test_get_mission_gaps(cube_source):
    from app.cube.handlers import get_mission

    result = get_mission(cube_source["caller"], {"key": cube_source["mission"]})
    assert "power_peak" in result.gaps and not result.values


def demo_caller(demo, username="demo"):
    from sqlalchemy import select

    from app import create_app
    from app.infrastructure.access import Caller
    from app.infrastructure.database import metadata as m

    app = create_app(demo["settings"])
    with demo["sessions"].begin() as session:
        user = session.execute(
            select(m.metadata.tables["users"].c.id).where(
                m.metadata.tables["users"].c.username == username
            )
        ).scalar_one()
    return Caller(
        user_id=user,
        roles=frozenset({"member"}),
        sessions=app.state.sessions,
        settings=demo["settings"],
    ), app


def test_find_missions_conditions(demo):
    import pytest

    from app.cube.handlers import find_missions
    from app.infrastructure.errors import InvalidInput

    caller, app = demo_caller(demo)
    conditions = [
        {"parameter": "mass_dry", "op": "le", "value": "150", "unit": "kg"},
        {"parameter": "orbit_altitude", "op": "le", "value": "600", "unit": "km"},
    ]
    try:
        request = {"conditions": conditions, "at": "2026-03-15", "stage": "current"}
        result = find_missions(caller, request)
        truth = demo["truth"]
        expected = set()
        for mission in truth["missions"]:
            rows = {
                v["parameter"]: v
                for v in truth["values"]
                if v["mission"] == mission["key"]
                and v["date"] <= "2026-03-15"
                and v["stage"] == mission["currentStage"]
            }
            if all(
                key in rows
                and rows[key]["kind"] in {"exact", "range"}
                and rows[key]["high"] <= bound
                for key, bound in [("mass_dry", 150), ("orbit_altitude", 600)]
            ):
                expected.add(mission["key"])
        matched = {row.mission.key for row in result.missions} == expected
        assert matched and expected
        with pytest.raises(InvalidInput):
            find_missions(caller, {"conditions": conditions[:1] * 13})
        with pytest.raises(InvalidInput):
            find_missions(caller, {"conditions": conditions, "limit": 21})
    finally:
        app.state.engine.dispose()


def test_access_before_counting(demo):
    import pytest

    from app.cube.handlers import find_missions, get_mission
    from app.infrastructure.errors import NotFound

    caller, app = demo_caller(demo, "restricted")
    try:
        result = find_missions(
            caller,
            {"conditions": [{"parameter": "mass_dry", "op": "ge", "value": "0", "unit": "kg"}]},
        )
        assert [row.mission.key for row in result.missions] == ["msn-sim-01"]
        assert not result.truncated
        with pytest.raises(NotFound):
            get_mission(caller, {"key": "msn-sim-02"})
    finally:
        app.state.engine.dispose()


def test_mcp_tools(cube_source):
    import asyncio

    from mcp.shared.memory import create_connected_server_and_client_session

    from app.infrastructure.mcp.server import build_server
    from app.infrastructure.operations.registry import registry

    server = build_server(registry, cube_source["caller"])

    async def run():
        async with create_connected_server_and_client_session(server) as session:
            result = await session.list_tools()
            assert {
                "list_parameters",
                "find_missions",
                "get_mission",
                "compare_missions",
                "get_value_evidence",
            } <= {tool.name for tool in result.tools}
            response = await session.call_tool("get_mission", {"key": cube_source["mission"]})
            assert not response.isError and response.structuredContent is not None

    asyncio.run(run())


def test_result_cap(cube_source):
    from uuid import uuid4

    from sqlalchemy import select

    from app.cube.handlers import find_missions
    from app.infrastructure.database import metadata as m

    install_units(cube_source)
    original = cube_source["add"]()
    keys = ["msn-" + uuid4().hex for _ in range(24)]
    try:
        with cube_source["owner"].begin() as connection:
            source = dict(
                connection.execute(select(m.cube_values).where(m.cube_values.c.id == original))
                .mappings()
                .one()
            )
            source.pop("id")
            for key in keys:
                connection.execute(
                    m.missions.insert().values(
                        key=key,
                        title="Synthetic cap mission",
                        current_stage="st3-design",
                        simulated=True,
                    )
                )
                connection.execute(
                    m.mission_grants.insert().values(
                        user_id=cube_source["caller"].user_id, mission_key=key
                    )
                )
                connection.execute(m.cube_values.insert().values(source | {"mission_key": key}))
        result = filter_source(cube_source)
        assert len(result.missions) == 20 and result.truncated
        requested = find_missions(
            cube_source["caller"],
            {
                "conditions": [{"parameter": "power_peak", "op": "ge", "value": "0", "unit": "W"}],
                "limit": 3,
            },
        )
        assert len(requested.missions) == 3 and requested.truncated
    finally:
        with cube_source["owner"].begin() as connection:
            for table in [m.cube_values, m.mission_grants]:
                connection.execute(table.delete().where(table.c.mission_key.in_(keys)))
            connection.execute(m.missions.delete().where(m.missions.c.key.in_(keys)))


def test_http_reads(cube_source):
    from fastapi.testclient import TestClient

    install_units(cube_source)
    identifier = cube_source["add"]()
    with TestClient(cube_source["app"], base_url="https://testserver") as client:
        assert client.get("/api/cube/parameters").status_code == 401
        signed = cube_source["sign_in"](client)
        assert signed.status_code == 200
        assert client.get("/api/cube/parameters").status_code == 200
        assert (
            client.get(
                "/api/cube/missions/" + cube_source["mission"], params={"stage": "current"}
            ).status_code
            == 200
        )
        assert client.get("/api/cube/values/" + str(identifier)).status_code == 200
        response = client.post(
            "/api/cube/find",
            json={
                "conditions": [{"parameter": "power_peak", "op": "le", "value": "450", "unit": "W"}]
            },
            headers={"Origin": "https://testserver"},
        )
        assert response.status_code == 200 and len(response.json()["missions"]) == 1
        assert (
            client.post(
                "/api/cube/compare",
                json={"keys": [cube_source["mission"]], "parameters": ["power_peak"]},
                headers={"Origin": "https://testserver"},
            ).status_code
            == 400
        )


def test_full_decimal_precision():
    from decimal import Decimal

    from app.cube.units import convert

    for value, factor, offset, expected in [
        ("12345678901234567890123456789", "1", "0", "12345678901234567890123456789"),
        ("12345678901234567890123456789", "1000", "0", "12345678901234567890123456789000"),
        ("1234567890" * 10, "1", "0", "1234567890" * 10),
        ("12345678901234567890123456789", "1", "0.01", "12345678901234567890123456789.01"),
    ]:
        result = convert(value, "W", {"W": (Decimal(factor), Decimal(offset))})
        correct = result == Decimal(expected)
        assert correct
