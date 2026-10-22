import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select, text

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


@pytest.mark.parametrize("failure", ["model", "write", "facet"])
def test_rebuild_failure_preserves_search_state(library, monkeypatch, failure):
    before = handles(library)
    with library["sessions"].begin() as session:
        versions = session.execute(select(m.doc_versions).order_by(m.doc_versions.c.id)).all()
        facets = session.execute(select(m.facet_texts).order_by(m.facet_texts.c.id)).all()
        assert facets

    def fail(*args, **kwargs):
        raise ApiError("Synthetic rebuild failure")

    with monkeypatch.context() as patch:
        if failure == "model":
            patch.setattr(index, "make_summary", fail)
            patch.setattr(index.time, "sleep", lambda _: None)
        elif failure == "write":
            clear = index.storage.clear_index

            def clear_then_fail(*args, **kwargs):
                clear(*args, **kwargs)
                fail()

            patch.setattr(index.storage, "clear_index", clear_then_fail)
        else:
            patch.setattr(index, "build_facets", fail)
        with pytest.raises(ApiError):
            build(library["settings"], rebuild=True)
    assert handles(library) == before
    with library["sessions"].begin() as session:
        assert (
            session.execute(select(m.doc_versions).order_by(m.doc_versions.c.id)).all() == versions
        )
        assert session.execute(select(m.facet_texts).order_by(m.facet_texts.c.id)).all() == facets
    build(library["settings"])
    assert handles(library) == before


def test_rebuild_keeps_search_rows_for_versions_that_no_longer_convert(library, monkeypatch):
    before = handles(library)

    def unsupported(*args, **kwargs):
        raise ValueError("Synthetic conversion failure")

    monkeypatch.setattr(index, "convert", unsupported)
    assert build(library["settings"], rebuild=True)["unsupported"] == 5
    assert handles(library) == before


def test_index_waits_for_database_lock_before_provider_scan(library, monkeypatch):
    provider = SimulatedProvider(library["root"])
    scanned = Event()
    scan = provider.scan

    def tracked_scan():
        scanned.set()
        return scan()

    monkeypatch.setattr(provider, "scan", tracked_scan)
    monkeypatch.setattr(index, "get_provider", lambda _: provider)
    engine = library["sessions"].kw["bind"]
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("SELECT pg_advisory_lock(1129464134, 1)"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(build, library["settings"])
            try:
                deadline = time.monotonic() + 5
                waiting = False
                while time.monotonic() < deadline:
                    waiting = connection.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                            "AND classid = 1129464134 AND objid = 1 AND NOT granted)"
                        )
                    ).scalar_one()
                    if waiting:
                        break
                    time.sleep(0.01)
                assert waiting and not scanned.is_set()
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(1129464134, 1)"))
            assert future.result(timeout=10) == {}
            assert scanned.is_set()


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
