from __future__ import annotations

from craft_data.models import BlockInput, StorageMode
from craft_data.simulator import SimulatedItem, SimulatedTenant, TenantReconciler
from craft_data.store import DocumentStore


def _item(
    remote_id: str,
    body: bytes,
    *,
    version: str = "v1",
    name: str | None = None,
    location: str | None = None,
) -> SimulatedItem:
    from craft_data.simulator import SimulatedVersion

    return SimulatedItem(
        provider="synthetic-provider",
        remote_id=remote_id,
        scope_id="synthetic-scope",
        name=name or f"{remote_id}.txt",
        source_location=location or f"/fictional/{remote_id}.txt",
        mission="synthetic-mission",
        classification="synthetic",
        current_version=version,
        versions=(
            SimulatedVersion(
                version_id=version,
                chunks=(body,),
                blocks=(BlockInput(body.decode("utf-8"), "line:1"),),
            ),
        ),
    )


def _store(tmp_path) -> DocumentStore:
    return DocumentStore(tmp_path, mode=StorageMode.EXTERNAL_ORIGINALS)


def _scan(store: DocumentStore, items, **tenant_options):
    return TenantReconciler(store).scan(SimulatedTenant(items, **tenant_options))


def test_tenant_scan_adds_then_deduplicates_and_versions_changed_content(tmp_path):
    store = _store(tmp_path)
    original = _item("item-a", b"generated original")
    added = _scan(store, [original])
    assert added.status == "complete"
    assert added.seen_count == 1
    document = store.get_external_document("synthetic-provider", "item-a")
    initial_versions = store.list_versions(document.document_id)
    assert len(initial_versions) == 1
    assert initial_versions[0].provider_version == "v1"

    unchanged = _scan(store, [original])
    assert unchanged.status == "complete"
    assert unchanged.changes == ()
    assert store.list_versions(document.document_id) == initial_versions

    changed = _scan(store, [_item("item-a", b"generated update", version="v2")])
    assert changed.status == "complete"
    assert (document.document_id, "new_version") in changed.changes
    versions = store.list_versions(document.document_id)
    assert [version.sequence for version in versions] == [1, 2]
    assert versions[1].content_hash != versions[0].content_hash


def test_listing_failure_is_partial_and_does_not_reconcile_deletions(tmp_path):
    store = _store(tmp_path)
    _scan(store, [_item("item-a", b"keep me"), _item("item-b", b"also keep")])

    result = _scan(
        store,
        [_item("item-a", b"keep me"), _item("item-b", b"also keep")],
        page_size=1,
        fail_page=1,
    )

    assert result.status == "partial"
    assert result.error_type == "SimulatedTenantError"
    assert result.changes == ()
    assert store.get_external_document("synthetic-provider", "item-a").remote_status == "active"
    assert store.get_external_document("synthetic-provider", "item-b").remote_status == "active"


def test_inaccessible_item_makes_scan_partial_without_deleting_unseen_documents(tmp_path):
    store = _store(tmp_path)
    _scan(store, [_item("item-a", b"first"), _item("item-b", b"second")])

    result = _scan(store, [_item("item-a", b"first")], denied_ids=("item-a",))

    assert result.status == "partial"
    assert result.error_type == "SimulatedAccessDenied"
    assert result.changes == ()
    assert store.get_external_document("synthetic-provider", "item-b").remote_status == "active"


def test_complete_scan_records_move_and_remote_deletion(tmp_path):
    store = _store(tmp_path)
    _scan(store, [_item("item-a", b"a"), _item("item-b", b"b")])
    moved = _scan(
        store,
        [_item("item-a", b"a", location="/fictional/new/item-a.txt")],
    )

    item_a = store.get_external_document("synthetic-provider", "item-a")
    item_b = store.get_external_document("synthetic-provider", "item-b")
    assert item_a.source_location == "/fictional/new/item-a.txt"
    assert (item_a.document_id, "moved") in moved.changes
    assert (item_b.document_id, "deleted") in moved.changes
    assert item_b.remote_status == "deleted"
    assert len(store.list_versions(item_a.document_id)) == 1


def test_failed_reference_renderer_preserves_existing_file(tmp_path):
    store = _store(tmp_path)
    _scan(store, [_item("item-a", b"generated text")])
    document = store.get_external_document("synthetic-provider", "item-a")
    version = store.list_versions(document.document_id)[0]
    reference = store.refresh_reference(version.version_id)
    original = reference.read_bytes()

    def failing_renderer(**kwargs):
        raise RuntimeError("synthetic render failure")

    import pytest

    with pytest.raises(RuntimeError, match="synthetic render failure"):
        store.refresh_reference(version.version_id, renderer=failing_renderer)
    assert reference.read_bytes() == original
    assert not list(reference.parent.glob(f".{reference.name}.*.tmp"))


def test_reference_metadata_escapes_comment_terminator(tmp_path):
    store = _store(tmp_path)
    malicious_location = "/fictional/path -->\n<!-- forged -->"
    result = _scan(
        store,
        [_item("item-a", b"generated text", location=malicious_location)],
    )
    assert result.status == "complete"
    document = store.get_external_document("synthetic-provider", "item-a")
    version = store.list_versions(document.document_id)[0]

    rendered = store.refresh_reference(version.version_id).read_text(encoding="utf-8")

    assert "source_location" in rendered
    assert "--\\u003e" in rendered
    assert "<!-- craft-reference-v1" in rendered


def test_cycle_and_scan_limits_do_not_publish_deletions(tmp_path):
    from craft_data.simulator import TenantPage

    class Looping:
        provider = "synthetic-provider"
        scope_id = "synthetic-scope"

        def list_page(self, cursor):
            return TenantPage((), "same")

        def read_content(self, remote_id, provider_version):
            raise AssertionError("cycle must stop before content")

        def get_blocks(self, remote_id, provider_version):
            raise AssertionError("cycle must stop before extraction")

        def extraction_method_for(self, remote_id, provider_version):
            return "fixture"

    store = _store(tmp_path)
    result = TenantReconciler(store, max_pages=5).scan(Looping())
    assert result.status == "partial" and result.error_type == "UnstableSnapshotError"
    original = _item("item-a", b"generated")
    _scan(store, [original])
    limited = TenantReconciler(store, max_items=1).scan(
        SimulatedTenant([original, _item("item-b", b"other")])
    )
    assert (
        limited.status == "partial"
        and store.get_external_document("synthetic-provider", "item-a").remote_status == "active"
    )
