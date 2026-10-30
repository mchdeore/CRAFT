"""Synthetic missions with source-backed budget tables."""

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import msgspec
import pytest
from sqlalchemy import Connection, and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from app import create_app
from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library.convert import convert
from app.tests.support import rows
from app.tests.support.people import person


@pytest.fixture
def cube_source(settings, owner_engine, tmp_path):
    configured = msgspec.structs.replace(
        settings, provider_root=str(tmp_path), allowed_origins="https://testserver"
    )
    mission = "msn-" + uuid4().hex
    with owner_engine.begin() as connection:
        connection.execute(
            m.missions.insert().values(
                key=mission, title="Synthetic mission", current_stage="st3-design", simulated=True
            )
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
    rows.load(owner_engine, provider)

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
            b for b in convert(provider.read_content(item, version), path) if b.kind == "table"
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

    with person(owner_engine) as account:
        with owner_engine.begin() as connection:
            connection.execute(
                m.mission_grants.insert().values(user_id=account.id, mission_key=mission, silo=None)
            )
        app = create_app(configured)
        caller = Caller(
            user_id=account.id,
            roles=frozenset({"member"}),
            sessions=app.state.sessions,
            settings=configured,
        )

        def sign_in(client):
            return client.post(
                "/api/session",
                json={"username": account.username, "password": account.password},
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
            "username": account.username,
            "sign_in": sign_in,
        }
        app.state.engine.dispose()
    with owner_engine.begin() as connection:
        facets = list(
            connection.execute(
                select(m.facet_texts.c.id).where(m.facet_texts.c.mission_key == mission)
            ).scalars()
        )
        connection.execute(
            m.vectors.delete().where(
                m.vectors.c.owner_kind == "facet_text", m.vectors.c.owner_id.in_(facets)
            )
        )
        connection.execute(m.facet_texts.delete().where(m.facet_texts.c.mission_key == mission))
        connection.execute(m.cube_values.delete().where(m.cube_values.c.mission_key == mission))
        connection.execute(m.elements.delete().where(m.elements.c.mission_key == mission))
        connection.execute(m.missions.delete().where(m.missions.c.key == mission))
        items = list(provider.items())
        clear_index(connection, items)
        connection.execute(m.provider_items.delete().where(m.provider_items.c.item.in_(items)))
        if not present:
            connection.execute(
                m.unit_conversions.delete().where(m.unit_conversions.c.parameter == "power_peak")
            )
            connection.execute(m.parameters.delete().where(m.parameters.c.key == "power_peak"))


def clear_index(connection: Connection, items: list[str]) -> None:
    """Remove what was indexed from these source items, and the facet texts that cite them."""
    documents = select(m.doc_versions.c.id).where(m.doc_versions.c.item.in_(items))
    chunks = select(m.chunks.c.id).where(m.chunks.c.doc_version_id.in_(documents))
    facets = select(m.facet_texts.c.id).where(m.facet_texts.c.sources.overlap(items))
    connection.execute(
        m.vectors.delete().where(
            or_(
                and_(m.vectors.c.owner_kind == "doc_version", m.vectors.c.owner_id.in_(documents)),
                and_(m.vectors.c.owner_kind == "chunk", m.vectors.c.owner_id.in_(chunks)),
                and_(m.vectors.c.owner_kind == "facet_text", m.vectors.c.owner_id.in_(facets)),
            )
        )
    )
    connection.execute(m.facet_texts.delete().where(m.facet_texts.c.id.in_(facets)))
    connection.execute(
        m.version_diffs.delete().where(
            or_(
                m.version_diffs.c.from_version.in_(documents),
                m.version_diffs.c.to_version.in_(documents),
            )
        )
    )
    connection.execute(m.chunks.delete().where(m.chunks.c.id.in_(chunks)))
    connection.execute(m.doc_versions.delete().where(m.doc_versions.c.id.in_(documents)))


BUDGET = b"# 1 Budget\n\n| Parameter | Value | Unit |\n|---|---|---|\n| Peak power | 350 | W |\n"


@pytest.fixture
def cube(cube_source):
    """cube_source with a second mission, power in W and kW, and callers with chosen grants.

    `value` and `facet` add rows to either mission, citing that mission's budget document unless
    `facet` is given other sources.
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

    def facet(mission, name, vector, sources=None):
        with owner.begin() as connection:
            text = connection.execute(
                m.facet_texts.insert()
                .values(
                    mission_key=mission,
                    facet=name,
                    text="Generated " + name,
                    sources=sources or [documents[mission]],
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
        return text

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
