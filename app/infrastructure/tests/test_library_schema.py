"""Library constraints, exact privileges, cascades and indexed search."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import Connection, Engine, inspect, select, text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import metadata as m
from app.infrastructure.tests.test_grants import assert_grants

LIBRARY = {"provider_items", "doc_versions", "chunks", "vectors", "version_diffs", "index_cursor"}


def add_version(connection: Connection, item: str, version: int = 1):
    return connection.execute(
        m.doc_versions.insert()
        .values(
            item=item,
            version=version,
            sha256=bytes(32),
            media_type="text/markdown",
            summary="Synthetic summary",
            summary_kind="extract",
        )
        .returning(m.doc_versions.c.id)
    ).scalar_one()


def add_item(connection: Connection) -> str:
    item = "itm_" + uuid4().hex[:12]
    connection.execute(
        m.provider_items.insert().values(
            item=item,
            path="reference/test.md",
            title="Synthetic",
            simulated=True,
            classification="unclassified",
        )
    )
    return item


def test_library_tables_exist(owner_engine: Engine) -> None:
    inspector = inspect(owner_engine)
    assert LIBRARY | {"mission_grants"} <= set(inspector.get_table_names())
    for name in LIBRARY | {"mission_grants"}:
        assert {c["name"] for c in inspector.get_columns(name)} == set(
            m.metadata.tables[name].c.keys()
        )
    for table, column in [("doc_versions", "summary_tsv"), ("chunks", "tsv")]:
        reflected = next(c for c in inspector.get_columns(table) if c["name"] == column)
        assert reflected["nullable"] is False
    embedding = next(
        c["type"] for c in inspector.get_columns("vectors") if c["name"] == "embedding"
    )
    assert str(embedding) == "VECTOR(1536)"


def test_app_reads_library_only(app: FastAPI, owner_engine: Engine) -> None:
    assert_grants(owner_engine, {n: {"SELECT"} for n in LIBRARY})
    for name in LIBRARY:
        with app.state.engine.connect() as connection:
            connection.execute(text(f"select * from {name} limit 1")).all()
        with (
            pytest.raises(DBAPIError, match="permission denied"),
            app.state.engine.begin() as connection,
        ):
            connection.execute(text(f"insert into {name} default values"))


def test_app_writes_grants(app: FastAPI) -> None:
    with app.state.engine.connect() as connection, connection.begin() as outer:
        user = connection.execute(
            text("insert into users (username, display_name) values (:name,'Test') returning id"),
            {"name": "test_" + uuid4().hex},
        ).scalar_one()
        grant = {"user_id": user, "mission_key": "msn-synthetic", "silo": None}
        connection.execute(m.mission_grants.insert().values(**grant))
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(m.mission_grants.insert().values(**grant))
        connection.execute(m.mission_grants.delete().where(m.mission_grants.c.user_id == user))
        assert (
            connection.execute(
                select(m.mission_grants.c.id).where(m.mission_grants.c.user_id == user)
            ).first()
            is None
        )
        outer.rollback()


def test_owner_writes_library_and_cascades(owner_engine: Engine) -> None:
    with owner_engine.connect() as connection, connection.begin() as outer:
        item = add_item(connection)
        old = add_version(connection, item)
        new = add_version(connection, item, 2)
        for block in [1, 2]:
            chunk = connection.execute(
                m.chunks.insert()
                .values(
                    doc_version_id=new,
                    block=block,
                    kind="text",
                    heading_path="Test",
                    locator=f"heading=1;block={block}",
                    text="Synthetic",
                    text_sha256=bytes(32),
                )
                .returning(m.chunks.c.id)
            ).scalar_one()
            connection.execute(
                m.vectors.insert().values(
                    owner_kind="chunk",
                    owner_id=chunk,
                    model="scripted",
                    recipe="chunk-v1",
                    embedding=[1.0] + [0.0] * 1535,
                )
            )
        connection.execute(
            m.version_diffs.insert().values(
                from_version=old, to_version=new, seq=1, change="changed", from_block=1, to_block=1
            )
        )
        connection.execute(m.doc_versions.delete().where(m.doc_versions.c.id == new))
        assert (
            connection.execute(
                select(m.chunks.c.id).where(m.chunks.c.doc_version_id == new)
            ).first()
            is None
        )
        assert (
            connection.execute(
                select(m.version_diffs.c.seq).where(m.version_diffs.c.to_version == new)
            ).first()
            is None
        )
        outer.rollback()


def test_item_and_hash_checks(owner_engine: Engine) -> None:
    with owner_engine.connect() as connection, connection.begin() as outer:
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(
                m.provider_items.insert().values(
                    item="invalid",
                    path="reference/test.md",
                    title="Test",
                    simulated=True,
                    classification="unclassified",
                )
            )
        item = add_item(connection)
        for hash_value, kind in [(bytes(31), "extract"), (bytes(32), "unknown")]:
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    m.doc_versions.insert().values(
                        item=item,
                        version=1,
                        sha256=hash_value,
                        media_type="text/plain",
                        summary="Synthetic",
                        summary_kind=kind,
                    )
                )
        outer.rollback()


def test_vector_cosine_search(owner_engine: Engine) -> None:
    with owner_engine.connect() as connection, connection.begin() as outer:
        ids = []
        for first, second in [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)]:
            ids.append(
                connection.execute(
                    m.vectors.insert()
                    .values(
                        owner_kind="chunk",
                        owner_id=uuid4(),
                        model="scripted",
                        recipe="chunk-v1",
                        embedding=[first, second] + [0.0] * 1534,
                    )
                    .returning(m.vectors.c.id)
                ).scalar_one()
            )
        query = [1.0] + [0.0] * 1535
        connection.execute(text("set local enable_seqscan = off"))
        connection.execute(text("set local hnsw.iterative_scan = 'strict_order'"))
        owned = (
            select(m.vectors.c.id, m.vectors.c.embedding)
            .where(m.vectors.c.id.in_(ids))
            .cte("owned_vectors")
            .prefix_with("MATERIALIZED")
        )
        statement = select(owned.c.id).order_by(owned.c.embedding.cosine_distance(query)).limit(1)
        assert connection.execute(statement).scalar_one() == ids[0]
        plan = (
            connection.execute(
                text(
                    "explain select id from vectors "
                    "order by embedding <=> cast(:query as vector) limit 1"
                ),
                {"query": "[" + ",".join(map(str, query)) + "]"},
            )
            .scalars()
            .all()
        )
        assert any("ix_vectors_embedding" in line for line in plan)
        outer.rollback()


def test_chunk_keyword_search(owner_engine: Engine) -> None:
    with owner_engine.connect() as connection, connection.begin() as outer:
        version = add_version(connection, add_item(connection))
        chunk = connection.execute(
            m.chunks.insert()
            .values(
                doc_version_id=version,
                block=1,
                kind="text",
                heading_path="Thermal",
                locator="heading=1;block=1",
                text="Synthetic radiator margin",
                text_sha256=bytes(32),
            )
            .returning(m.chunks.c.id)
        ).scalar_one()
        found = (
            connection.execute(
                text(
                    "select id from chunks where doc_version_id=:version "
                    "and tsv @@ websearch_to_tsquery('english', 'radiator margin')"
                ),
                {"version": version},
            )
            .scalars()
            .all()
        )
        assert found == [chunk]
        outer.rollback()


def test_workspace_file_checks(owner_engine: Engine) -> None:
    with owner_engine.connect() as connection, connection.begin() as outer:
        user = connection.execute(
            text("insert into users (username, display_name) values (:name,'Test') returning id"),
            {"name": "test_" + uuid4().hex},
        ).scalar_one()
        conversation = connection.execute(
            text("insert into conversations (owner_id,title) values (:id,'Test') returning id"),
            {"id": user},
        ).scalar_one()
        for state, classification in [("unknown", "unclassified"), ("ready", "unknown")]:
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    m.workspace_files.insert().values(
                        conversation_id=conversation,
                        name="test.txt",
                        media_type="text/plain",
                        size_bytes=0,
                        sha256=bytes(32),
                        origin="upload",
                        state=state,
                        classification=classification,
                        simulated=True,
                    )
                )
        outer.rollback()
