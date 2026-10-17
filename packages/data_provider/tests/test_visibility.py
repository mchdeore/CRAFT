from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

import craft_data.relations as relation_module
from craft_data import (
    BlockInput,
    DocumentStore,
    SearchQuery,
    StorageMode,
    add_evidence_edge,
    add_similarity_candidate,
    add_structural_edge,
    export_graph,
    insert_embedding,
    list_relations,
    search,
)
from craft_data.models import RepresentationKind
from craft_data.navigation import DocumentQuery, query_documents
from craft_data.timeutil import dump_datetime

NOW = datetime(2026, 10, 17, 16, tzinfo=UTC)


class Clock:
    value = NOW

    def __call__(self):
        return self.value


@pytest.fixture
def source_pair(tmp_path):
    clock = Clock()
    store = DocumentStore(tmp_path, mode=StorageMode.LOCAL_ORIGINALS, clock=clock)
    documents = []
    versions = []
    for name, mission in [("left", "alpha"), ("right", "beta")]:
        document = store.create_local_document(
            name=name,
            source_location=f"{name}.txt",
            mission=mission,
            classification="generated",
            synthetic=True,
        )
        documents.append(document)
        versions.append(ingest(store, document.document_id, name.encode(), f"{name} baseline"))
        store.set_mission_permission(principal="reader", mission=mission, can_read=True)
    return store, clock, documents, versions


def ingest(store, document_id, body, text, *, method="fixture-v1"):
    return store.ingest_local(
        document_id,
        body,
        (BlockInput(text=text, heading="Evidence", location="line:1"),),
        extraction_method=method,
    )


def edge(store, versions, kind):
    left, right = versions
    if kind == "structural":
        return add_structural_edge(
            store,
            source_version_id=left.version_id,
            target_version_id=right.version_id,
            relation="depends_on",
        )
    if kind == "evidence":
        block = store.get_blocks(left.version_id)[0]
        return add_evidence_edge(
            store,
            source_version_id=left.version_id,
            source_block_id=block.block_id,
            target_version_id=right.version_id,
            relation="supports",
            evidence_hash=block.text_hash,
        )
    embeddings = []
    for version in versions:
        with store.read_connection() as connection:
            representation_id = connection.execute(
                "SELECT representation_id FROM representations WHERE extraction_id=?",
                (version.extraction_id,),
            ).fetchone()[0]
        embeddings.append(
            insert_embedding(
                store,
                representation_id,
                (1.0, 0.0),
                model="generated-test",
                dimensions=2,
                input_type="search_document",
                recipe="body",
            )
        )
    return add_similarity_candidate(
        store,
        source_embedding_id=embeddings[0].embedding_id,
        target_embedding_id=embeddings[1].embedding_id,
        score=1.0,
    )


@pytest.mark.parametrize("kind", ["structural", "evidence", "similarity_candidate"])
@pytest.mark.parametrize("endpoint", [0, 1])
def test_withdrawal_hides_both_relation_and_graph_endpoints(source_pair, kind, endpoint):
    store, _, documents, versions = source_pair
    relation = edge(store, versions, kind)
    assert list_relations(store, principal="reader") == (relation,)
    before = export_graph(store, principal="reader")
    with store.transaction() as connection:
        connection.execute(
            "UPDATE documents SET remote_status='deleted' WHERE document_id=?",
            (documents[endpoint].document_id,),
        )
    assert list_relations(store, principal="reader") == ()
    graph = export_graph(store, principal="reader")
    assert graph["legend"] == before["legend"]
    assert graph["edges"] == []
    assert {node["id"] for node in graph["nodes"]} == {versions[1 - endpoint].source_key}
    assert list_relations(store, principal="reader", version_mode="all", known_at=NOW) == ()
    historical = export_graph(store, principal="reader", version_mode="all", known_at=NOW)
    assert historical == graph
    assert search(store, SearchQuery("baseline", "reader", known_at=NOW)).total_hits == 1


@pytest.mark.parametrize("kind", ["structural", "evidence", "similarity_candidate"])
@pytest.mark.parametrize("mission", ["alpha", "beta"])
def test_revoking_either_mission_hides_edges_without_counts(source_pair, kind, mission):
    store, _, _, versions = source_pair
    edge(store, versions, kind)
    before = export_graph(store, principal="reader")
    store.set_mission_permission(principal="reader", mission=mission, can_read=False)
    assert list_relations(store, principal="reader") == ()
    graph = export_graph(store, principal="reader")
    assert graph["legend"] == before["legend"]
    assert graph["edges"] == []
    visible = versions[1] if mission == "alpha" else versions[0]
    assert {node["id"] for node in graph["nodes"]} == {visible.source_key}
    assert export_graph(store, principal="reader", version_mode="all", known_at=NOW) == graph
    assert search(store, SearchQuery("baseline", "reader", known_at=NOW)).total_hits == 1


@pytest.mark.parametrize("kind", ["structural", "evidence", "similarity_candidate"])
@pytest.mark.parametrize("endpoint", [0, 1])
def test_latest_version_is_selected_before_relation_matching(source_pair, kind, endpoint):
    store, clock, documents, versions = source_pair
    relation = edge(store, versions, kind)
    clock.value += timedelta(hours=1)
    newest = ingest(store, documents[endpoint].document_id, b"new bytes", "replacement")
    assert list_relations(store, principal="reader") == ()
    assert list_relations(store, principal="reader", version_mode="all") == (relation,)
    assert list_relations(store, principal="reader", known_at=NOW) == (relation,)
    assert list_relations(store, principal="reader", version_mode="all", known_at=NOW) == (
        relation,
    )
    current_graph = export_graph(store, principal="reader")
    assert current_graph["edges"] == []
    assert {node["id"] for node in current_graph["nodes"]} == {
        newest.source_key,
        versions[1 - endpoint].source_key,
    }
    all_graph = export_graph(store, principal="reader", version_mode="all")
    assert len(all_graph["nodes"]) == 3
    assert [item["id"] for item in all_graph["edges"]] == [relation.edge_id]
    graph = export_graph(store, principal="reader", known_at=NOW)
    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) == 1
    assert {node["id"] for node in graph["nodes"]} == {version.source_key for version in versions}


@pytest.mark.parametrize("kind", ["structural", "evidence", "similarity_candidate"])
def test_future_edge_is_not_a_historical_relation(source_pair, kind):
    store, clock, _, versions = source_pair
    clock.value += timedelta(hours=1)
    relation = edge(store, versions, kind)
    assert list_relations(store, principal="reader", known_at=NOW) == ()
    assert export_graph(store, principal="reader", known_at=NOW)["edges"] == []
    assert list_relations(store, principal="reader", known_at=clock.value) == (relation,)
    assert [
        item["id"]
        for item in export_graph(store, principal="reader", known_at=clock.value)["edges"]
    ] == [relation.edge_id]


def test_historical_search_uses_the_extraction_known_then(source_pair):
    store, clock, documents, versions = source_pair
    old_block = store.get_blocks(versions[0].version_id)[0]
    clock.value += timedelta(hours=1)
    new = ingest(store, documents[0].document_id, b"left", "future revision", method="fixture-v2")
    assert new.version_id == versions[0].version_id
    assert new.extraction_id != versions[0].extraction_id
    before = search(store, SearchQuery("baseline", "reader", known_at=NOW))
    assert before.total_hits == 2
    hit = next(hit for hit in before.hits if hit.document_id == documents[0].document_id)
    assert hit.excerpt == "Evidence\nleft baseline"
    assert hit.citation.block_id == old_block.block_id
    assert hit.citation.location == old_block.location
    assert hit.citation.source_key == old_block.source_key
    assert hit.citation.content_hash == versions[0].content_hash
    assert search(store, SearchQuery("future", "reader", known_at=NOW)).total_hits == 0
    assert search(store, SearchQuery("future", "reader")).total_hits == 1


@pytest.mark.parametrize("kind", ["structural", "evidence", "similarity_candidate"])
@pytest.mark.parametrize("endpoint", [0, 1])
def test_reextraction_retires_only_extraction_dependent_edges(source_pair, kind, endpoint):
    store, clock, documents, versions = source_pair
    relation = edge(store, versions, kind)
    clock.value += timedelta(hours=1)
    ingest(
        store,
        documents[endpoint].document_id,
        ("left" if endpoint == 0 else "right").encode(),
        "reprocessed",
        method="fixture-v2",
    )
    survives = kind == "structural" or (kind == "evidence" and endpoint == 1)
    assert list_relations(store, principal="reader") == ((relation,) if survives else ())
    assert list_relations(store, principal="reader", known_at=NOW) == (relation,)


def test_late_generated_summary_cannot_leak_into_known_time_search(source_pair):
    store, clock, _, versions = source_pair
    clock.value += timedelta(hours=1)
    store.add_representation(
        versions[0].version_id,
        kind="document_summary",
        purpose="lookup",
        text="future synthesis",
    )
    query = SearchQuery(
        text="synthesis", principal="reader", kinds=(RepresentationKind.DOCUMENT_SUMMARY,)
    )
    assert search(store, replace(query, known_at=NOW)).total_hits == 0
    assert search(store, replace(query, known_at=clock.value)).total_hits == 1
    assert search(store, query).total_hits == 1
    assert (
        query_documents(
            store, "reader", DocumentQuery(text="synthesis", known_at=NOW)
        ).total_visible_versions
        == 0
    )
    assert (
        query_documents(
            store, "reader", DocumentQuery(text="synthesis", known_at=clock.value)
        ).total_visible_versions
        == 1
    )
    assert (
        query_documents(store, "reader", DocumentQuery(text="synthesis")).total_visible_versions
        == 1
    )


def test_tied_extraction_times_select_one_latest_attempt(source_pair):
    store, _, documents, versions = source_pair
    ingest(store, documents[0].document_id, b"left", "tied newest", method="fixture-v2")
    assert search(store, SearchQuery("left", "reader", known_at=NOW)).total_hits == 0
    newest = search(store, SearchQuery("newest", "reader", known_at=NOW))
    assert newest.total_hits == 1
    assert newest.hits[0].citation.content_hash == versions[0].content_hash
    assert (
        query_documents(
            store, "reader", DocumentQuery(text="left baseline", known_at=NOW)
        ).total_visible_versions
        == 0
    )
    assert (
        query_documents(
            store, "reader", DocumentQuery(text="newest", known_at=NOW)
        ).total_visible_versions
        == 1
    )


def test_legacy_representation_migration_preserves_data_without_inventing_summary_times(
    source_pair,
):
    store, clock, _, versions = source_pair
    summary_id = store.add_representation(
        versions[0].version_id,
        kind="document_summary",
        purpose="legacy lookup",
        text="legacy synthesis",
    )
    other_extracted_id = store.add_representation(
        versions[0].version_id,
        kind="body_chunk",
        purpose="body_heading_v1",
        text="legacy delayed extraction",
        block_id=store.get_blocks(versions[0].version_id)[0].block_id,
        origin="extracted",
    )
    relations = [
        edge(store, versions, kind) for kind in ("structural", "evidence", "similarity_candidate")
    ]
    with store.transaction() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(representations)")}
        if "created_at" in columns:
            connection.execute("ALTER TABLE representations DROP COLUMN created_at")
    before = stored_records(store)
    clock.value += timedelta(hours=1)
    reopened = DocumentStore(store.root, mode=StorageMode.LOCAL_ORIGINALS, clock=clock)
    assert search(reopened, SearchQuery("baseline", "reader", known_at=NOW)).total_hits == 2
    query = SearchQuery(
        text="synthesis", principal="reader", kinds=(RepresentationKind.DOCUMENT_SUMMARY,)
    )
    assert search(reopened, query).total_hits == 1
    assert search(reopened, replace(query, known_at=clock.value)).total_hits == 0
    assert reopened.get_representation(summary_id)["created_at"] is None
    assert reopened.get_representation(other_extracted_id)["created_at"] is None
    assert search(reopened, SearchQuery("delayed", "reader")).total_hits == 1
    assert search(reopened, SearchQuery("delayed", "reader", known_at=clock.value)).total_hits == 0
    assert (
        query_documents(
            reopened, "reader", DocumentQuery(text="synthesis", known_at=clock.value)
        ).total_visible_versions
        == 0
    )
    assert {
        item.edge_id for item in list_relations(reopened, principal="reader", known_at=NOW)
    } == {item.edge_id for item in relations}
    assert stored_records(reopened) == before
    with reopened.read_connection() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    twice = DocumentStore(store.root, mode=StorageMode.LOCAL_ORIGINALS, clock=clock)
    assert search(twice, SearchQuery("baseline", "reader", known_at=NOW)).total_hits == 2
    assert stored_records(twice) == before
    with twice.read_connection() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def stored_records(store):
    tables = (
        "documents",
        "doc_versions",
        "extractions",
        "blocks",
        "representations",
        "embedding_payloads",
        "embeddings",
        "structural_edges",
        "evidence_edges",
        "similarity_candidates",
        "mission_grants",
    )
    with store.read_connection() as connection:
        return {
            table: [
                {
                    key: value
                    for key, value in dict(row).items()
                    if table != "representations" or key != "created_at"
                }
                for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
            ]
            for table in tables
        }


@pytest.mark.parametrize("origin", ["extracted", "generated", "explicit", "suggested"])
def test_new_representation_times_are_exact_normalized_and_immutable(source_pair, origin):
    store, clock, _, versions = source_pair
    with store.read_connection() as connection:
        rows = connection.execute(
            "SELECT r.created_at, x.created_at AS extracted_at FROM representations r "
            "JOIN extractions x ON x.extraction_id=r.extraction_id"
        ).fetchall()
    assert all(row["created_at"] == row["extracted_at"] == dump_datetime(NOW) for row in rows)
    clock.value = NOW.astimezone(timezone(timedelta(hours=-4))) + timedelta(microseconds=1)
    inputs = dict(kind="document_summary", purpose="lookup", text="timed synthesis", origin=origin)
    representation_id = store.add_representation(versions[0].version_id, **inputs)
    created_at = dump_datetime(clock.value)
    assert store.get_representation(representation_id)["created_at"] == created_at
    query = SearchQuery(
        text="synthesis", principal="reader", kinds=(RepresentationKind.DOCUMENT_SUMMARY,)
    )
    assert search(store, replace(query, known_at=NOW)).total_hits == 0
    assert search(store, replace(query, known_at=clock.value)).total_hits == 1
    clock.value += timedelta(hours=1)
    assert store.add_representation(versions[0].version_id, **inputs) == representation_id
    assert store.get_representation(representation_id)["created_at"] == created_at


def test_graph_uses_one_read_snapshot_for_edges_and_nodes(source_pair, monkeypatch):
    store, _, _, versions = source_pair
    relation = edge(store, versions, "structural")
    with store.read_connection() as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    original_selector = relation_module._select_relations

    def select_then_revoke(connection, scope):
        result = original_selector(connection, scope)
        store.set_mission_permission(principal="reader", mission="beta", can_read=False)
        return result

    monkeypatch.setattr(relation_module, "_select_relations", select_then_revoke)
    graph = export_graph(store, principal="reader")
    assert [item["id"] for item in graph["edges"]] == [relation.edge_id]
    assert {node["id"] for node in graph["nodes"]} == {version.source_key for version in versions}
    assert search(store, SearchQuery("baseline", "reader")).total_hits == 1


@pytest.mark.parametrize(
    "kwargs", [{"version_mode": "bogus"}, {"principal": ""}, {"known_at": datetime(2026, 10, 17)}]
)
def test_invalid_relation_scope_fails_before_returning_data(source_pair, kwargs, monkeypatch):
    store, _, _, versions = source_pair
    edge(store, versions, "structural")

    def unexpected_read():
        pytest.fail("invalid scope must fail before opening a data connection")

    monkeypatch.setattr(store, "read_connection", unexpected_read)
    for reader in (list_relations, export_graph):
        with pytest.raises(ValueError):
            reader(store, **({"principal": "reader"} | kwargs))
    with pytest.raises(ValueError):
        search(store, SearchQuery(text="!!!", kinds=(), **({"principal": "reader"} | kwargs)))
