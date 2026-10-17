from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from craft_data import (
    BlockInput,
    CubeQuery,
    CubeValueInput,
    DocumentStore,
    ExternalOriginalUnavailableError,
    ExternalRecord,
    FakeEmbedding,
    SearchQuery,
    StorageMode,
    ValueState,
    add_evidence_edge,
    add_similarity_candidate,
    add_structural_edge,
    export_graph,
    get_embedding,
    insert_cube_value,
    insert_embedding,
    list_relations,
    query_cube,
    search,
)

NOW = datetime(2025, 1, 1, tzinfo=UTC)


@pytest.fixture
def local_store(tmp_path):
    return DocumentStore(tmp_path / "local", mode=StorageMode.LOCAL_ORIGINALS, clock=lambda: NOW)


def make_local(store, *, mission="mission-a", name="fixture report", source="fixture.txt"):
    return store.create_local_document(
        name=name,
        source_location=source,
        mission=mission,
        classification="generated",
        synthetic=True,
    )


def ingest(store, document_id, body, text, *, provider_version=None, location="line:1"):
    return store.ingest_local(
        document_id,
        body,
        (BlockInput(text=text, location=location, heading="Results"),),
        extraction_method="fixture-text-v1",
        provider_version=provider_version,
    )


def test_identity_is_opaque_and_content_revisions_are_immutable(local_store):
    document = make_local(local_store)
    original_id = document.document_id
    first = ingest(local_store, original_id, b"A", "Alpha")
    unchanged = ingest(local_store, original_id, b"A", "Alpha")
    second = ingest(local_store, original_id, b"B", "Beta")
    third = ingest(local_store, original_id, b"A", "Alpha")

    assert original_id != document.name
    assert (
        local_store.rename_document(
            original_id, name="renamed fixture", source_location="new/path.txt"
        ).document_id
        == original_id
    )
    assert unchanged.version_id == first.version_id
    assert [v.version_id for v in local_store.list_versions(original_id)] == [
        first.version_id,
        second.version_id,
        third.version_id,
    ]
    assert [v.sequence for v in local_store.list_versions(original_id)] == [1, 2, 3]
    assert len({first.version_id, second.version_id, third.version_id}) == 3
    assert [local_store.read_version_bytes(v.version_id) for v in (first, second, third)] == [
        b"A",
        b"B",
        b"A",
    ]


def test_extraction_citations_and_markdown_reference_retain_provenance(local_store):
    document = make_local(local_store)
    version = ingest(
        local_store,
        document.document_id,
        b"# Results\n\nGenerated evidence: 42 kg.",
        "Generated evidence: 42 kg.",
    )
    blocks = local_store.get_blocks(version.version_id)
    assert len(blocks) == 1
    assert blocks[0].heading == "Results"
    assert blocks[0].location == "line:1"
    assert len(blocks[0].text_hash) == 64

    citation = local_store.citation(version.version_id, block_id=blocks[0].block_id)
    assert citation.content_hash == version.content_hash
    assert citation.source_key == blocks[0].source_key
    reference = local_store.refresh_reference(version.version_id)
    rendered = reference.read_text(encoding="utf-8")
    assert "# fixture report" in rendered
    assert "Generated evidence: 42 kg." in rendered
    assert "Results" in rendered
    assert version.version_id not in rendered  # IDs remain opaque, not display content.


def test_external_originals_keep_provider_reference_without_source_binary(tmp_path):
    store = DocumentStore(
        tmp_path / "external", mode=StorageMode.EXTERNAL_ORIGINALS, clock=lambda: NOW
    )
    record = ExternalRecord(
        provider="fixture-provider",
        remote_id="opaque-item-9",
        scope_id="fixture-scope",
        name="generated external",
        source_location="folder/generated.txt",
        mission="mission-a",
        silo=None,
        stage=None,
        classification="generated",
        synthetic=True,
        provider_version="provider-rev-1",
        body=b"generated source bytes",
        blocks=(BlockInput("Derived text", "line:1"),),
        extraction_method="fixture-v1",
    )

    document, version = store.ingest_external(record)
    assert document.remote_id == record.remote_id
    assert document.document_id != record.remote_id
    assert version.provider_version == record.provider_version
    assert version.blob_path is None
    assert (
        store.get_external_document(record.provider, record.remote_id).document_id
        == document.document_id
    )
    with pytest.raises(ExternalOriginalUnavailableError):
        store.read_version_bytes(version.version_id)
    assert (
        not list((tmp_path / "external" / "originals").glob("**/*"))
        if (tmp_path / "external" / "originals").exists()
        else True
    )


def test_search_is_deterministic_cited_and_permission_filtered(local_store):
    visible = make_local(local_store, mission="visible")
    hidden = make_local(local_store, mission="hidden")
    v1 = ingest(local_store, visible.document_id, b"visible", "quartz metric seven")
    ingest(local_store, hidden.document_id, b"hidden", "quartz metric nine")
    local_store.set_mission_permission(principal="reader", mission="visible", can_read=True)

    page = search(local_store, SearchQuery(text="quartz", principal="reader"))
    assert page.total_hits == 1
    assert len(page.hits) == 1
    hit = page.hits[0]
    assert hit.document_id == visible.document_id
    assert hit.citation.version == f"sha256:{v1.content_hash}#1"
    assert hit.citation.location == "line:1"
    assert search(local_store, SearchQuery(text="quartz", principal="outsider")).total_hits == 0


def test_embedding_payload_provenance_and_relation_graph_are_typed(local_store):
    left = make_local(local_store, name="left")
    right = make_local(local_store, name="right")
    left_version = ingest(local_store, left.document_id, b"left", "shared generated phrase")
    right_version = ingest(local_store, right.document_id, b"right", "another generated phrase")
    left_block = local_store.get_blocks(left_version.version_id)[0]
    right_block = local_store.get_blocks(right_version.version_id)[0]
    left_rep = local_store.add_representation(
        left_version.version_id,
        kind="facet",
        purpose="fixture-facet",
        text="generated topic: measurement",
        block_id=left_block.block_id,
        origin="generated",
        generator_model="fixture-model",
        recipe="facet-v1",
    )
    right_rep = local_store.add_representation(
        right_version.version_id,
        kind="facet",
        purpose="fixture-facet",
        text="generated topic: measurement",
        block_id=right_block.block_id,
        origin="generated",
        generator_model="fixture-model",
        recipe="facet-v1",
    )
    vector = FakeEmbedding.embed("generated topic: measurement", dimensions=8)
    left_embedding = insert_embedding(
        local_store,
        left_rep,
        vector,
        model=FakeEmbedding.model,
        dimensions=8,
        input_type="facet",
        recipe=FakeEmbedding.recipe,
    )
    right_embedding = insert_embedding(
        local_store,
        right_rep,
        vector,
        model=FakeEmbedding.model,
        dimensions=8,
        input_type="facet",
        recipe=FakeEmbedding.recipe,
    )
    assert get_embedding(local_store, left_embedding.embedding_id).vector == vector
    assert left_embedding.payload_id == right_embedding.payload_id
    assert local_store.get_representation(left_rep)["origin"] == "generated"

    structural = add_structural_edge(
        local_store,
        source_version_id=left_version.version_id,
        target_version_id=right_version.version_id,
        relation="references",
    )
    evidence = add_evidence_edge(
        local_store,
        source_version_id=left_version.version_id,
        source_block_id=left_block.block_id,
        target_version_id=right_version.version_id,
        relation="supports",
        evidence_hash=left_block.text_hash,
    )
    similarity = add_similarity_candidate(
        local_store,
        source_embedding_id=left_embedding.embedding_id,
        target_embedding_id=right_embedding.embedding_id,
        score=1.0,
    )
    local_store.set_mission_permission(principal="reader", mission="mission-a", can_read=True)
    edges = list_relations(local_store, principal="reader")
    assert {edge.edge_id for edge in edges} == {
        structural.edge_id,
        evidence.edge_id,
        similarity.edge_id,
    }
    graph = export_graph(local_store, principal="reader")
    assert len(graph["nodes"]) == 2
    similarity_edge = next(
        edge for edge in graph["edges"] if edge["type"] == "similarity_candidate"
    )
    assert similarity_edge["model"] == FakeEmbedding.model
    assert similarity_edge["recipe"] == FakeEmbedding.recipe
    assert similarity_edge["score"] == 1.0
    assert "suggestion" in graph["legend"]["similarity_candidate"]


def test_cube_uses_permissions_event_and_knowledge_time_and_unit_conversion(local_store):
    document = make_local(local_store)
    version = ingest(local_store, document.document_id, b"evidence", "mass evidence")
    block = local_store.get_blocks(version.version_id)[0]
    local_store.set_mission_permission(principal="reader", mission="mission-a", can_read=True)

    def add(amount, event_days, known_days):
        return insert_cube_value(
            local_store,
            CubeValueInput(
                mission="mission-a",
                metric="mass",
                subject="sample-1",
                state=ValueState.VALUE,
                amount=Decimal(amount),
                unit="kg",
                event_time=NOW + timedelta(days=event_days),
                known_at=NOW + timedelta(days=known_days),
                source_version_id=version.version_id,
                source_block_id=block.block_id,
                source_location=block.location,
                evidence_hash=block.text_hash,
            ),
        )

    older = add("2", 1, 1)
    newer = add("3", 2, 2)
    selected = query_cube(
        local_store,
        CubeQuery(
            principal="reader",
            mission="mission-a",
            metric="mass",
            at=NOW + timedelta(days=5),
            known_at=NOW + timedelta(days=5),
            target_unit="g",
        ),
    )
    assert selected.accessible
    assert len(selected.rows) == 1
    assert selected.rows[0].amount == Decimal("3000")
    assert selected.rows[0].unit == "g"
    assert selected.rows[0].fact_id == newer
    historical = query_cube(
        local_store,
        CubeQuery(
            principal="reader",
            mission="mission-a",
            metric="mass",
            at=NOW + timedelta(days=5),
            known_at=NOW + timedelta(days=1),
        ),
    )
    assert historical.rows[0].fact_id == older
    denied = query_cube(
        local_store,
        CubeQuery(
            principal="outsider",
            mission="mission-a",
            metric="mass",
            at=NOW + timedelta(days=5),
            known_at=NOW + timedelta(days=5),
        ),
    )
    assert not denied.accessible
    assert denied.rows == ()


def test_cube_rejects_mismatched_evidence_and_incompatible_units(local_store):
    document = make_local(local_store)
    version = ingest(local_store, document.document_id, b"evidence", "valid evidence")
    block = local_store.get_blocks(version.version_id)[0]
    bad = CubeValueInput(
        mission="mission-a",
        metric="mass",
        subject="sample",
        state=ValueState.VALUE,
        amount=Decimal("1"),
        unit="kg",
        event_time=NOW,
        known_at=NOW,
        source_version_id=version.version_id,
        source_block_id=block.block_id,
        source_location=block.location,
        evidence_hash="0" * 64,
    )
    with pytest.raises(ValueError, match="evidence hash"):
        insert_cube_value(local_store, bad)
    from craft_data import convert_unit

    with pytest.raises(ValueError, match="incompatible"):
        convert_unit(Decimal("1"), "kg", "kWh")


def test_generated_csv_extraction_preserves_rows_and_locations():
    from craft_data.extraction import extract_csv

    blocks = extract_csv(b"item,mass\nsample-1,4\nsample-2,7\n")
    assert [(block.location, block.text) for block in blocks] == [
        ("row:2", "item=sample-1 | mass=4"),
        ("row:3", "item=sample-2 | mass=7"),
    ]


def test_candidate_publication_rejects_replay_and_rolls_back_invalid_evidence(local_store):
    from dataclasses import replace

    from craft_data.cube import (
        FakeApprovalBoundary,
        create_candidate_batch,
        publish_candidate_batch,
    )
    from craft_data.models import CandidateValueInput

    document = make_local(local_store)
    version = ingest(local_store, document.document_id, b"evidence", "generated evidence")
    block = local_store.get_blocks(version.version_id)[0]
    value = CandidateValueInput(
        mission="mission-a",
        metric="mass",
        subject="sample",
        amount=Decimal("2"),
        unit="kg",
        event_time=NOW,
        source_version_id=version.version_id,
        source_block_id=block.block_id,
        source_location=block.location,
        evidence_hash=block.text_hash,
    )
    batch = create_candidate_batch(local_store, [value])
    boundary = FakeApprovalBoundary()
    with local_store.read_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM cube_values").fetchone()[0] == 0
    rejected = publish_candidate_batch(
        local_store, batch.batch_id, boundary.decide(batch, accepted=False)
    )
    assert rejected.inserted_count == 0
    accepted = boundary.decide(batch, accepted=True)
    assert publish_candidate_batch(local_store, batch.batch_id, accepted).inserted_count == 1
    assert publish_candidate_batch(local_store, batch.batch_id, accepted).inserted_count == 0
    with pytest.raises(ValueError, match="evidence hash"):
        create_candidate_batch(local_store, [value, replace(value, evidence_hash="0" * 64)])
    with local_store.read_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidate_batches").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM candidate_values").fetchone()[0] == 1


def test_withdrawn_latest_evidence_does_not_return_a_value_or_revive_older(local_store):
    local_store.set_mission_permission(principal="reader", mission="mission-a", can_read=True)
    documents = [make_local(local_store, name=name) for name in ["old", "new"]]
    for index, document in enumerate(documents):
        version = ingest(local_store, document.document_id, str(index).encode(), "mass evidence")
        block = local_store.get_blocks(version.version_id)[0]
        insert_cube_value(
            local_store,
            CubeValueInput(
                mission="mission-a",
                metric="mass",
                subject="sample",
                state=ValueState.VALUE,
                amount=Decimal(index + 1),
                unit="kg",
                event_time=NOW + timedelta(days=index),
                known_at=NOW,
                source_version_id=version.version_id,
                source_block_id=block.block_id,
                source_location=block.location,
                evidence_hash=block.text_hash,
            ),
        )
    with local_store.transaction() as connection:
        connection.execute(
            "UPDATE documents SET remote_status='deleted' WHERE document_id=?",
            (documents[1].document_id,),
        )
    result = query_cube(
        local_store,
        CubeQuery(
            principal="reader",
            mission="mission-a",
            metric="mass",
            at=NOW + timedelta(days=3),
            known_at=NOW + timedelta(days=3),
        ),
    )
    assert result.rows[0].state is ValueState.WITHHELD
    assert result.rows[0].amount is None and result.rows[0].source is None


def test_exact_unit_conversion_preserves_long_decimal():
    from craft_data import convert_unit

    assert convert_unit(Decimal("123456789012345678901234567890.123"), "kg", "g") == Decimal(
        "123456789012345678901234567890123"
    )


def test_latest_and_historical_search_choose_version_before_ranking(tmp_path):
    clock = [NOW]
    store = DocumentStore(
        tmp_path / "versions", mode=StorageMode.LOCAL_ORIGINALS, clock=lambda: clock[0]
    )
    document = make_local(store)
    first = ingest(store, document.document_id, b"old", "thermal power 80 watts")
    clock[0] = NOW + timedelta(days=2)
    second = ingest(store, document.document_id, b"new", "thermal power 105 watts")
    store.set_mission_permission(principal="reader", mission="mission-a", can_read=True)
    latest = search(store, SearchQuery("thermal power", "reader"))
    assert latest.total_hits == 1
    assert latest.hits[0].citation.content_hash == second.content_hash
    history = search(
        store, SearchQuery("thermal power", "reader", known_at=NOW + timedelta(days=1))
    )
    assert history.total_hits == 1
    assert history.hits[0].citation.content_hash == first.content_hash
    all_versions = search(
        store, SearchQuery("thermal power", "reader", limit=1, version_mode="all")
    )
    assert all_versions.total_hits == 2 and len(all_versions.hits) == 1
    assert search(store, SearchQuery("absent token", "reader")).total_hits == 0
    assert search(store, SearchQuery("thermal", "outsider")).total_hits == 0
