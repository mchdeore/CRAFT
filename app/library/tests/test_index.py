import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event

import msgspec
import pytest
from sqlalchemy import Text, func, select, text

from app.infrastructure.database import metadata as m
from app.infrastructure.errors import ApiError
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library import enrichment, index
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


def test_index_source_limit_does_not_persist_oversized_version(library):
    provider = SimulatedProvider(library["root"])
    provider.write_content("reference/generated-oversized.md", b"Generated oversized source")
    item = next(
        row["item"]
        for row in provider.items().values()
        if row["path"] == "reference/generated-oversized.md"
    )
    configured = msgspec.structs.replace(library["settings"], index_max_bytes=4)
    assert build(configured)["oversized"] == 1
    with library["sessions"].begin() as session:
        assert (
            session.execute(
                select(m.doc_versions.c.id).where(m.doc_versions.c.item == item)
            ).scalar_one_or_none()
            is None
        )


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


@pytest.mark.parametrize("media_type", [None, 7])
def test_ocr_enabled_index_skips_unsupported_versions_with_invalid_media_type(
    library, monkeypatch, media_type
):
    provider = SimulatedProvider(library["root"])
    provider.write_content("reference/unsupported.bin", b"Synthetic unsupported content")
    provider.write_content("reference/supported.md", b"# Generated supported document")
    records = provider.items()
    unsupported = next(record for record in records.values() if record["path"].endswith(".bin"))
    unsupported["versions"][0]["media_type"] = media_type
    monkeypatch.setattr(provider, "items", lambda: records)
    monkeypatch.setattr(index, "get_provider", lambda _: provider)
    monkeypatch.setattr(enrichment, "enqueue_version_jobs", lambda *_: 0)

    result = build(library["settings"], enrich_ocr=True)

    assert result["unsupported"] == 1
    assert result["versions"] == 1
    with library["sessions"].begin() as session:
        assert (
            session.execute(
                select(m.doc_versions.c.id).where(m.doc_versions.c.item == unsupported["item"])
            ).first()
            is None
        )


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


def test_incremental_change_counts_by_kind(library) -> None:
    provider = SimulatedProvider(library["root"])
    records = sorted(provider.items().values(), key=lambda row: row["item"])
    assert len(records) >= 3
    changed, moved, deleted = records[:3]

    provider.write_content(changed["path"], b"# Changed\n\nSynthetic change for indexing.")
    old_path = moved["path"]
    stem, dot, extension = old_path.rpartition(".")
    moved_path = f"{stem}-moved.{extension}" if dot else old_path + "-moved"
    provider.path(old_path).rename(provider.path(moved_path))
    provider.scan()
    provider.delete_item(deleted["item"])
    provider.write_content("reference/new-item.md", b"# Added\n\nSynthetic added document.")

    counts = build(library["settings"])
    assert counts["new_items"] == 1
    assert counts["changed_items"] == 1
    assert counts["moved_items"] == 1
    assert counts["deleted_items"] == 1
    assert build(library["settings"]) == {}


def test_enrich_ocr_enqueues_only_current_ocr_only_versions(library, monkeypatch) -> None:
    from pypdf import PdfWriter

    provider = SimulatedProvider(library["root"])
    for width in (72, 73):
        writer = PdfWriter()
        writer.add_blank_page(width=width, height=72)
        output = BytesIO()
        writer.write(output)
        provider.write_content("reference/ocr-history.pdf", output.getvalue())
    record = next(
        row for row in provider.items().values() if row["path"] == "reference/ocr-history.pdf"
    )
    latest_version = provider.get_item(record["item"])["versions"][-1]["version"]
    captured: dict[str, list[tuple[str, int]]] = {}

    def fake_enqueue(settings, versions, *, requested_by=None, force=False):
        _ = (settings, requested_by, force)
        captured["versions"] = list(versions)
        return len(captured["versions"])

    monkeypatch.setattr(enrichment, "enqueue_version_jobs", fake_enqueue)
    counts = build(library["settings"], enrich_ocr=True)

    assert counts["ocr_jobs_enqueued"] == 1
    assert captured["versions"] == [(record["item"], latest_version)]


def test_ocr_opt_in_skips_text_native_versions(library, monkeypatch):
    captured = []

    def enqueue(settings, versions):
        captured.extend(versions)
        return len(versions)

    monkeypatch.setattr(enrichment, "enqueue_version_jobs", enqueue)
    result = build(library["settings"], enrich_ocr=True)
    assert result.get("ocr_jobs_enqueued", 0) == 0
    assert result.get("versions", 0) == 0
    assert captured == []


def test_ocr_enqueue_outage_is_recoverable_after_versions_commit(library, monkeypatch):
    from pypdf import PdfWriter

    from app.library.queue import QueueUnavailable

    provider = SimulatedProvider(library["root"])
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    output = BytesIO()
    writer.write(output)
    provider.write_content("reference/outage.pdf", output.getvalue())

    def outage(*args, **kwargs):
        raise QueueUnavailable()

    with monkeypatch.context() as patch:
        patch.setattr(enrichment, "enqueue_version_jobs", outage)
        with pytest.raises(QueueUnavailable):
            build(library["settings"], enrich_ocr=True)
    captured = []

    def enqueue(settings, versions):
        captured.extend(versions)
        return len(versions)

    monkeypatch.setattr(enrichment, "enqueue_version_jobs", enqueue)
    result = build(library["settings"], enrich_ocr=True)
    assert result["ocr_jobs_enqueued"] == 1
    assert result.get("versions", 0) == 0
    assert len(captured) == 1


@pytest.mark.parametrize("filename", ["scan.pdf", "scan.png"])
def test_scanned_inputs_have_ocr_only_versions_without_embeddings(library, monkeypatch, filename):
    from pypdf import PdfWriter

    if filename.endswith(".pdf"):
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        output = BytesIO()
        writer.write(output)
        content = output.getvalue()
    else:
        import base64

        content = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY1sAAAAASUVORK5CYII="
        )
    provider = SimulatedProvider(library["root"])
    path = "reference/" + filename
    provider.write_content(path, content)
    record = next(value for value in provider.items().values() if value["path"] == path)
    captured = []

    def enqueue(settings, versions):
        captured.extend(versions)
        return len(versions)

    def unexpected_model_call(*args, **kwargs):
        raise AssertionError("OCR-only indexing must not call a model")

    monkeypatch.setattr(enrichment, "enqueue_version_jobs", enqueue)
    monkeypatch.setattr(index, "embed_many", unexpected_model_call)
    monkeypatch.setattr(index, "make_summary", unexpected_model_call)
    build(library["settings"], enrich_ocr=True)
    assert (record["item"], 1) in captured
    with library["sessions"].begin() as session:
        version = (
            session.execute(select(m.doc_versions).where(m.doc_versions.c.item == record["item"]))
            .mappings()
            .one()
        )
        assert version["summary"] == ""
        assert (
            session.execute(
                select(func.count())
                .select_from(m.chunks)
                .where(m.chunks.c.doc_version_id == version["id"])
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                select(func.count())
                .select_from(m.vectors)
                .where(
                    m.vectors.c.owner_kind == "doc_version", m.vectors.c.owner_id == version["id"]
                )
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("failure", [None, "model", "write", "ocr", "facet"])
def test_rebuild_preserves_ocr_and_search_state_on_failure(library, monkeypatch, failure):
    if failure == "ocr":
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        output = BytesIO()
        writer.write(output)
        provider = SimulatedProvider(library["root"])
        provider.write_content("reference/rebuild-ocr.pdf", output.getvalue())
        # Establish an eligible OCR-only version before snapshotting the retained state.
        with monkeypatch.context() as patch:
            patch.setattr(
                enrichment, "enqueue_version_jobs", lambda settings, versions: len(versions)
            )
            build(library["settings"], enrich_ocr=True)
    with library["sessions"].begin() as session:
        version = session.execute(select(m.doc_versions.c.id).limit(1)).scalar_one()
        session.execute(
            m.doc_versions.update()
            .where(m.doc_versions.c.id == version)
            .values(ocr_text="Generated retained OCR", ocr_model="synthetic-ocr")
        )
    before = handles(library)
    with library["sessions"].begin() as session:
        facet_query = (
            select(m.facet_texts, m.vectors.c.embedding.cast(Text).label("embedding"))
            .join(
                m.vectors,
                (m.vectors.c.owner_kind == "facet_text")
                & (m.vectors.c.owner_id == m.facet_texts.c.id),
            )
            .order_by(m.facet_texts.c.id)
        )
        before_facets = session.execute(facet_query).all()
        assert before_facets
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise ApiError("Synthetic rebuild failure")

        if failure == "model":
            patch.setattr(index, "make_summary", fail)
            patch.setattr(index.time, "sleep", lambda _: None)
        elif failure == "write":
            original = index.storage.clear_index

            def clear_then_fail(*args, **kwargs):
                original(*args, **kwargs)
                fail()

            patch.setattr(index.storage, "clear_index", clear_then_fail)
        elif failure == "ocr":
            patch.setattr(enrichment, "enqueue_version_jobs", fail)
        elif failure == "facet":
            patch.setattr(index, "build_facets", fail)
        if failure:
            with pytest.raises(ApiError):
                build(library["settings"], rebuild=True, enrich_ocr=failure == "ocr")
            with library["sessions"].begin() as session:
                assert session.execute(facet_query).all() == before_facets
        else:
            build(library["settings"], rebuild=True)
    assert handles(library) == before
    build(library["settings"])
    assert handles(library) == before
    with library["sessions"].begin() as session:
        assert (
            session.execute(
                select(m.doc_versions.c.ocr_text).where(m.doc_versions.c.id == version)
            ).scalar_one()
            == "Generated retained OCR"
        )
