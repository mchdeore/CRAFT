from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from craft_data.models import BlockInput, StorageMode
from craft_data.navigation import (
    DocumentQuery,
    FieldVector,
    TagFilter,
    query_documents,
    rank_fields,
    version_timeline,
)
from craft_data.store import DocumentStore


class Clock:
    def __init__(self, start: datetime) -> None:
        self._value = start

    def __call__(self) -> datetime:
        out = self._value
        self._value = self._value + timedelta(seconds=1)
        return out


@pytest.fixture()
def seeded_store(tmp_path):
    clock = Clock(datetime(2024, 1, 1, tzinfo=UTC))
    store = DocumentStore(tmp_path / "db", mode=StorageMode.LOCAL_ORIGINALS, clock=clock)
    d1 = store.create_local_document(
        name="Alpha 100% Report",
        source_location="/src/a",
        mission="m1",
        classification="c1",
        synthetic=True,
        silo="s1",
        stage="st1",
    )
    d2 = store.create_local_document(
        name="Hidden",
        source_location="/src/b",
        mission="m2",
        classification="c1",
        synthetic=True,
    )
    v1 = store.ingest_local(
        d1.document_id,
        b"one",
        [BlockInput(text="progress 100%", location="L1")],
        extraction_method="m",
    )
    v2 = store.ingest_local(
        d1.document_id,
        b"two",
        [BlockInput(text="second body", location="L2")],
        extraction_method="m",
    )
    _ = store.ingest_local(
        d2.document_id, b"x", [BlockInput(text="private", location="L")], extraction_method="m"
    )
    store.assign_tag(
        d1.document_id,
        version_id=v1.version_id,
        label_key="k",
        label_value="old",
        origin="explicit",
        evidence_block_id=None,
    )
    store.assign_tag(
        d1.document_id,
        version_id=v2.version_id,
        label_key="k",
        label_value="new",
        origin="explicit",
        evidence_block_id=None,
    )
    with store.transaction() as c:
        c.execute(
            "INSERT INTO mission_grants(principal, mission, can_read) VALUES (?, ?, 1)",
            ("reader", "m1"),
        )
        c.execute(
            "INSERT INTO mission_grants(principal, mission, can_read) VALUES (?, ?, 0)",
            ("reader", "m2"),
        )
    return store, d1.document_id, v1.version_id, v2.version_id


def test_combined_filters_unicode_and_literal_percent(seeded_store):
    store, _, _, _ = seeded_store
    q = DocumentQuery(
        missions=("m1",),
        silos=("s1",),
        stages=("st1",),
        classifications=("c1",),
        providers=("local",),
        text="100%",
        version_mode="latest",
    )
    page = query_documents(store, "reader", q)
    assert page.total_visible_versions == 1
    assert page.records[0].name == "Alpha 100% Report"


def test_permission_filters_before_counts_and_page(seeded_store):
    store, _, _, _ = seeded_store
    page = query_documents(store, "reader", DocumentQuery(version_mode="all", limit=100))
    assert page.total_visible_versions == 2
    assert len(page.records) == 2


def test_latest_all_and_known_at(seeded_store):
    store, _, v1, v2 = seeded_store
    all_page = query_documents(store, "reader", DocumentQuery(version_mode="all"))
    latest_page = query_documents(store, "reader", DocumentQuery(version_mode="latest"))
    assert {r.version_id for r in all_page.records} == {v1, v2}
    assert {r.version_id for r in latest_page.records} == {v2}
    cutoff = min(r.recorded_at for r in all_page.records)
    known = query_documents(store, "reader", DocumentQuery(version_mode="latest", known_at=cutoff))
    assert {r.version_id for r in known.records} == {v1}


def test_current_version_tags_only(seeded_store):
    store, _, v1, v2 = seeded_store
    old_tag = query_documents(
        store, "reader", DocumentQuery(tags=(TagFilter("k", "old"),), version_mode="latest")
    )
    new_tag = query_documents(
        store, "reader", DocumentQuery(tags=(TagFilter("k", "new"),), version_mode="latest")
    )
    assert old_tag.total_visible_versions == 0
    assert new_tag.records[0].version_id == v2
    all_old = query_documents(
        store, "reader", DocumentQuery(tags=(TagFilter("k", "old"),), version_mode="all")
    )
    assert all_old.records[0].version_id == v1


def test_unknown_principal_and_timeline_privacy(seeded_store):
    store, doc_id, _, _ = seeded_store
    page = query_documents(store, "nobody", DocumentQuery())
    assert page.total_visible_versions == 0
    assert version_timeline(store, "nobody", doc_id) == ()
    assert len(version_timeline(store, "reader", doc_id)) == 2


def test_invalid_query_types_and_limits(seeded_store):
    store, _, _, _ = seeded_store
    with pytest.raises(TypeError):
        query_documents(store, "reader", DocumentQuery(limit=True))
    with pytest.raises(ValueError):
        query_documents(store, "reader", DocumentQuery(limit=101))
    with pytest.raises(TypeError):
        query_documents(store, "reader", DocumentQuery(known_at=datetime(2024, 1, 1)))
    with pytest.raises(TypeError):
        query_documents(store, "reader", DocumentQuery(missions=("ok", 3)))  # type: ignore[arg-type]


def test_rank_fields_dimension_and_nonfinite_failures():
    def vector(values, model="m"):
        return FieldVector(tuple(values), model, 2, "r")

    with pytest.raises(ValueError):
        rank_fields([1.0, 2.0], {"text": vector([1.0])}, model="m", dimensions=2, recipe="r")
    with pytest.raises(ValueError):
        rank_fields(
            [1.0, float("nan")], {"text": vector([1.0, 0])}, model="m", dimensions=2, recipe="r"
        )
    with pytest.raises(TypeError):
        rank_fields([1.0, 0], {"text": vector([True, 0])}, model="m", dimensions=2, recipe="r")
    with pytest.raises(ValueError):
        rank_fields(
            [1.0, 0], {"text": vector([1.0, 0], model="other")}, model="m", dimensions=2, recipe="r"
        )
    ranked = rank_fields(
        [1.0, 1.0],
        {"text": vector([1000, 0]), "missions": vector([1, 1])},
        model="m",
        dimensions=2,
        recipe="r",
    )
    assert ranked[0][0] == "missions" and ranked[0][1] == pytest.approx(1)
    assert ranked[1][1] == pytest.approx(2**-0.5)


def test_old_body_is_not_resurrected_by_latest_filter(seeded_store):
    store, doc_id, v1, v2 = seeded_store
    assert query_documents(store, "reader", DocumentQuery(text="progress")).records == ()
    history = query_documents(store, "reader", DocumentQuery(text="progress", version_mode="all"))
    assert [r.version_id for r in history.records] == [v1]


def test_known_time_selects_historical_extraction_and_hides_later_tags(seeded_store):
    store, doc_id, v1, v2 = seeded_store
    before = store.get_version(v2).extracted_at
    store.ingest_local(
        doc_id,
        b"two",
        [BlockInput(text="new interpretation", location="L3")],
        extraction_method="replacement",
    )
    old = query_documents(store, "reader", DocumentQuery(text="second body", known_at=before))
    assert old.records and old.records[0].version_id == v2
    assert (
        query_documents(
            store, "reader", DocumentQuery(text="new interpretation", known_at=before)
        ).records
        == ()
    )
    assert query_documents(store, "reader", DocumentQuery(text="second body")).records == ()
    early = store.get_version(v1).extracted_at
    assert (
        query_documents(
            store, "reader", DocumentQuery(tags=(TagFilter("k", "old"),), known_at=early)
        ).records
        == ()
    )


def test_document_tags_and_empty_pages_keep_permitted_total(seeded_store):
    store, doc_id, _, _ = seeded_store
    store.assign_tag(doc_id, label_key="purpose", label_value="reference", origin="explicit")
    page = query_documents(
        store, "reader", DocumentQuery(tags=(TagFilter("purpose", "reference"),), offset=50)
    )
    assert page.total_visible_versions == 1 and page.records == ()
