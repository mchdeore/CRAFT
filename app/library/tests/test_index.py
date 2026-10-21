from sqlalchemy import select

from app.infrastructure.database import metadata as m
from app.infrastructure.errors import ApiError
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library import index
from app.library.index import build


def handles(library):
    items = list(SimulatedProvider(library["root"]).items())
    with library["sessions"].begin() as session:
        return session.execute(
            select(
                m.doc_versions.c.item,
                m.doc_versions.c.version,
                m.chunks.c.block,
                m.chunks.c.text_sha256,
            )
            .join(m.chunks, m.chunks.c.doc_version_id == m.doc_versions.c.id)
            .where(m.doc_versions.c.item.in_(items))
            .order_by(m.doc_versions.c.item, m.doc_versions.c.version, m.chunks.c.block)
        ).all()


def test_index_idempotent(library) -> None:
    before = handles(library)
    assert build(library["settings"]) == {}
    assert handles(library) == before


def test_rebuild_keeps_handles(library) -> None:
    before = sorted(handles(library))
    build(library["settings"], True)
    assert sorted(handles(library)) == before


def test_generated_summary(library) -> None:
    items = list(SimulatedProvider(library["root"]).items())
    with library["sessions"].begin() as session:
        versions = (
            session.execute(select(m.doc_versions).where(m.doc_versions.c.item.in_(items)))
            .mappings()
            .all()
        )
    assert len(versions) == 5 and all(
        v["summary_kind"] == "generated" and len(v["summary"].split()) <= 150 for v in versions
    )


def test_vectors_per_record(library) -> None:
    items = list(SimulatedProvider(library["root"]).items())
    documents = select(m.doc_versions.c.id).where(m.doc_versions.c.item.in_(items))
    owned_chunks = select(m.chunks.c.id).where(m.chunks.c.doc_version_id.in_(documents))
    with library["sessions"].begin() as session:
        vectors = (
            session.execute(
                select(m.vectors).where(
                    m.vectors.c.owner_kind.in_(["chunk", "doc_version"]),
                    m.vectors.c.owner_id.in_(documents.union_all(owned_chunks)),
                )
            )
            .mappings()
            .all()
        )
        chunks = session.execute(owned_chunks).all()
    assert len(vectors) == len(chunks) + 5
    assert all(
        v["model"] == "text-embedding-3-large" and v["recipe"] in ["chunk-v1", "doc-v1"]
        for v in vectors
    )


def test_unsupported_format_continues(library) -> None:
    provider = SimulatedProvider(library["root"])
    provider.write_content("reference/test.docx", b"Synthetic")
    assert build(library["settings"])["unsupported"] == 1
    assert len(handles(library)) > 0


def test_failed_model_call_is_retried(library, monkeypatch) -> None:
    provider = SimulatedProvider(library["root"])
    provider.write_content("reference/retry.md", b"# Retry\n\nSynthetic retried text.")
    summarize, calls = index.make_summary, []

    def flaky(caller, blocks):
        calls.append(1)
        if len(calls) == 1:
            raise ApiError()
        return summarize(caller, blocks)

    monkeypatch.setattr(index, "make_summary", flaky)
    monkeypatch.setattr(index.time, "sleep", lambda seconds: None)
    build(library["settings"])
    assert len(calls) == 2
