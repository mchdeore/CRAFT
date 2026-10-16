"""Source-backed edge cases and the separate full demonstration database."""

import hashlib
from datetime import UTC, date, datetime
from uuid import uuid4

import msgspec
import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app import create_app
from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library.handlers import convert_source, index_provider
from app.seed.tests.test_demo import demo as demo


@pytest.fixture
def cube_source(settings, owner_engine, tmp_path):
    configured = msgspec.structs.replace(
        settings, provider_root=str(tmp_path), allowed_origins="https://testserver"
    )
    mission = "msn-" + uuid4().hex
    username = "cube_" + uuid4().hex
    password = uuid4().hex
    with owner_engine.begin() as connection:
        user = connection.execute(
            m.metadata.tables["users"]
            .insert()
            .values(username=username, display_name="Synthetic cube account")
            .returning(m.metadata.tables["users"].c.id)
        ).scalar_one()
        connection.execute(
            m.metadata.tables["user_roles"].insert().values(user_id=user, role="member")
        )
        connection.execute(
            m.metadata.tables["local_credentials"]
            .insert()
            .values(user_id=user, password_hash=PasswordHasher().hash(password))
        )
        connection.execute(
            m.missions.insert().values(
                key=mission, title="Synthetic mission", current_stage="st3-design", simulated=True
            )
        )
        connection.execute(
            m.mission_grants.insert().values(user_id=user, mission_key=mission, silo=None)
        )
        present = connection.execute(
            select(m.parameters.c.key).where(m.parameters.c.key == "power_peak")
        ).first()
        connection.execute(
            insert(m.parameters)
            .values(key="power_peak", label="Peak power", unit="W", min_value=0)
            .on_conflict_do_nothing()
        )
    provider = SimulatedProvider(tmp_path, clock="2026-01-01T00:00:00+00:00")
    path = f"missions/{mission}/engineering/st3-design/budget.md"
    initial = (
        b"# 1 Budget\n\n| Parameter | Value | Unit |\n|---|---|---|\n| Peak power | 400 | W |\n"
    )
    provider.write_content(path, initial)
    item = next(iter(provider.items()))
    index_provider(configured)
    app = create_app(configured)
    caller = Caller(
        user_id=user, roles=frozenset({"member"}), sessions=app.state.sessions, settings=configured
    )

    def add(
        value=400,
        valid=date(2026, 1, 1),
        recorded=datetime(2026, 1, 10, tzinfo=UTC),
        kind="exact",
        high=None,
        version=1,
        element=None,
        identifier=None,
        source_number=None,
        source_unit=None,
    ):
        block = next(
            b
            for b in convert_source(provider.read_content(item, version), path)
            if b.kind == "table"
        )
        row = block.text.splitlines()[2]
        values = {
            "mission_key": mission,
            "silo": "engineering",
            "stage": "st3-design",
            "parameter": "power_peak",
            "kind": kind,
            "low": value,
            "high": value if high is None else high,
            "valid_from": valid,
            "recorded_at": recorded,
            "item": item,
            "version": version,
            "block": block.block,
            "locator": block.locator + ";row=1",
            "quote_sha256": hashlib.sha256(row.encode()).digest(),
            "simulated": True,
            "element_id": element,
            "source_number": source_number,
            "source_unit": source_unit,
        }
        if kind in {"unknown", "not_applicable"}:
            values.update(low=None, high=None)
        if identifier is not None:
            values["id"] = identifier
        with owner_engine.begin() as connection:
            return connection.execute(
                m.cube_values.insert().values(**values).returning(m.cube_values.c.id)
            ).scalar_one()

    def sign_in(client):
        return client.post(
            "/api/session",
            json={"username": username, "password": password},
            headers={"Origin": "https://testserver"},
        )

    yield {
        "caller": caller,
        "app": app,
        "mission": mission,
        "provider": provider,
        "path": path,
        "item": item,
        "add": add,
        "owner": owner_engine,
        "settings": configured,
        "username": username,
        "sign_in": sign_in,
    }
    app.state.engine.dispose()
    with owner_engine.begin() as connection:
        connection.execute(m.cube_values.delete().where(m.cube_values.c.mission_key == mission))
        connection.execute(m.elements.delete().where(m.elements.c.mission_key == mission))
        connection.execute(m.mission_grants.delete().where(m.mission_grants.c.user_id == user))
        connection.execute(m.missions.delete().where(m.missions.c.key == mission))
        docs = list(
            connection.execute(
                select(m.doc_versions.c.id).where(m.doc_versions.c.item == item)
            ).scalars()
        )
        chunks = list(
            connection.execute(
                select(m.chunks.c.id).where(m.chunks.c.doc_version_id.in_(docs))
            ).scalars()
        )
        connection.execute(m.vectors.delete().where(m.vectors.c.owner_id.in_([*docs, *chunks])))
        connection.execute(m.doc_versions.delete().where(m.doc_versions.c.item == item))
        connection.execute(m.provider_items.delete().where(m.provider_items.c.item == item))
        connection.execute(
            m.index_cursor.delete().where(m.index_cursor.c.provider == str(tmp_path.resolve()))
        )
        for name in ["auth_sessions", "local_credentials", "user_roles"]:
            table = m.metadata.tables[name]
            connection.execute(table.delete().where(table.c.user_id == user))
        connection.execute(
            m.metadata.tables["users"].delete().where(m.metadata.tables["users"].c.id == user)
        )
        if not present:
            connection.execute(
                m.unit_conversions.delete().where(m.unit_conversions.c.parameter == "power_peak")
            )
            connection.execute(m.parameters.delete().where(m.parameters.c.key == "power_peak"))
