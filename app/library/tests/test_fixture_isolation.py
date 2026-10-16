"""A library fixture must not remove another corpus's rows."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.infrastructure.database import metadata as m
from app.library.index import build
from app.tests.support.library import library_context


@pytest.mark.parametrize("rebuild", [False, True])
def test_library_teardown_preserves_unrelated_corpus(settings, owner_engine, tmp_path, rebuild):
    item = "itm_" + uuid4().hex[:12]
    mission = "msn-" + uuid4().hex
    documents = [uuid4(), uuid4()]
    chunk, facet = uuid4(), uuid4()
    cursor = "sentinel-" + uuid4().hex
    with owner_engine.begin() as connection:
        connection.execute(
            m.missions.insert().values(
                key=mission,
                title="Synthetic unrelated mission",
                current_stage="st3-design",
                simulated=True,
            )
        )
        connection.execute(
            m.provider_items.insert().values(
                item=item,
                path="sentinel.md",
                title="Synthetic unrelated source",
                mission_key=mission,
                simulated=True,
                classification="unclassified",
            )
        )
        for version, identifier in enumerate(documents, 1):
            connection.execute(
                m.doc_versions.insert().values(
                    id=identifier,
                    item=item,
                    version=version,
                    sha256=b"x" * 32,
                    media_type="text/plain",
                    summary="Synthetic unrelated source",
                    summary_kind="extract",
                )
            )
        connection.execute(
            m.chunks.insert().values(
                id=chunk,
                doc_version_id=documents[0],
                block=1,
                kind="text",
                heading_path="Sentinel",
                locator="paragraph:1",
                text="Synthetic unrelated block",
                text_sha256=b"x" * 32,
            )
        )
        connection.execute(
            m.facet_texts.insert().values(
                id=facet,
                mission_key=mission,
                facet="purpose",
                text="Synthetic unrelated facet",
                sources=[item],
                model="fake",
            )
        )
        for kind, identifier in [("chunk", chunk), ("facet_text", facet)]:
            connection.execute(
                m.vectors.insert().values(
                    owner_kind=kind,
                    owner_id=identifier,
                    model="fake-embed@1",
                    recipe="sentinel",
                    embedding=[1.0] + [0.0] * 1535,
                )
            )
        connection.execute(
            m.version_diffs.insert().values(
                from_version=documents[0],
                to_version=documents[1],
                seq=1,
                change="changed",
                from_block=1,
                to_block=1,
            )
        )
        connection.execute(m.index_cursor.insert().values(provider=cursor, cursor=3))
    try:
        with library_context(settings, tmp_path) as state:
            if rebuild:
                build(state["settings"], rebuild=True)
        with owner_engine.connect() as connection:
            rows = [
                (m.provider_items, m.provider_items.c.item == item, 1),
                (m.doc_versions, m.doc_versions.c.id.in_(documents), 2),
                (m.chunks, m.chunks.c.id == chunk, 1),
                (m.facet_texts, m.facet_texts.c.id == facet, 1),
                (m.vectors, m.vectors.c.owner_id.in_([chunk, facet]), 2),
                (m.version_diffs, m.version_diffs.c.from_version == documents[0], 1),
                (m.index_cursor, m.index_cursor.c.provider == cursor, 1),
            ]
            for table, condition, expected in rows:
                actual = connection.execute(
                    select(func.count()).select_from(table).where(condition)
                ).scalar_one()
                assert actual == expected, table.name
    finally:
        with owner_engine.begin() as connection:
            connection.execute(m.vectors.delete().where(m.vectors.c.owner_id.in_([chunk, facet])))
            connection.execute(m.facet_texts.delete().where(m.facet_texts.c.id == facet))
            connection.execute(
                m.version_diffs.delete().where(m.version_diffs.c.from_version.in_(documents))
            )
            connection.execute(m.doc_versions.delete().where(m.doc_versions.c.id.in_(documents)))
            connection.execute(m.provider_items.delete().where(m.provider_items.c.item == item))
            connection.execute(m.index_cursor.delete().where(m.index_cursor.c.provider == cursor))
            connection.execute(m.missions.delete().where(m.missions.c.key == mission))
