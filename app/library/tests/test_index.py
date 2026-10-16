from sqlalchemy import select

from app.infrastructure.database import metadata as m
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library.index import build


def handles(library):
    with library["sessions"].begin() as session:
        return session.execute(
            select(
                m.doc_versions.c.item,
                m.doc_versions.c.version,
                m.chunks.c.block,
                m.chunks.c.text_sha256,
            ).join(m.chunks, m.chunks.c.doc_version_id == m.doc_versions.c.id)
        ).all()


def test_index_idempotent(library) -> None:
    before = handles(library)
    assert build(library["settings"]) == {}
    assert handles(library) == before


def test_rebuild_keeps_handles(library) -> None:
    before = sorted(handles(library))
    build(library["settings"], True)
    assert sorted(handles(library)) == before


def test_extract_summary(library) -> None:
    with library["sessions"].begin() as session:
        versions = session.execute(select(m.doc_versions)).mappings().all()
    assert len(versions) == 5 and all(
        v["summary_kind"] == "extract" and len(v["summary"].split()) <= 150 for v in versions
    )


def test_vectors_per_record(library) -> None:
    with library["sessions"].begin() as session:
        vectors = session.execute(select(m.vectors)).mappings().all()
        chunks = session.execute(select(m.chunks.c.id)).all()
    assert len(vectors) == len(chunks) + 5
    assert all(
        v["model"] == "fake-embed@1" and v["recipe"] in ["chunk-v1", "doc-v1"] for v in vectors
    )


def test_unsupported_format_continues(library) -> None:
    provider = SimulatedProvider(library["root"])
    provider.write_content("reference/test.docx", b"Synthetic")
    assert build(library["settings"])["unsupported"] == 1
    assert len(handles(library)) > 0
