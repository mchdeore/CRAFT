from __future__ import annotations

import httpx
import pytest

from craft_data.opentext import MissingExtractionCapability, OpenTextError, OpenTextTenant


def _tenant(transport: httpx.BaseTransport, token_calls: list[str], **kw):
    client = httpx.Client(transport=transport)
    return OpenTextTenant(
        cms_url="https://us.api.opentext.com/cms",
        css_url="https://css.opentext.com",
        root_type="doc_type",
        root_id="root-1",
        mission="m1",
        classification="trusted",
        token_provider=lambda: token_calls.append("t") or "token",
        http_client=client,
        synthetic=True,
        **kw,
    )


def test_list_page_maps_and_cursor_same_origin():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if "page=2" in str(req.url):
            return httpx.Response(200, json={"_embedded": {"collection": []}, "_links": {}})
        return httpx.Response(
            200,
            json={
                "_embedded": {
                    "collection": [
                        {
                            "category": "file",
                            "type": "invoice",
                            "id": "v-1",
                            "chronicle_id": "c-1",
                            "name": "n1",
                        }
                    ]
                },
                "_links": {"next": {"href": "https://us.api.opentext.com/cms/next?page=2"}},
            },
        )

    calls: list[str] = []
    t = _tenant(httpx.MockTransport(handler), calls)
    p1 = t.list_page(None)
    assert p1.items[0].remote_id == "c-1"
    assert p1.items[0].current_version == "v-1"
    p2 = t.list_page(p1.next_cursor)
    assert p2.items == ()
    assert len(calls) == 2


def test_cross_origin_cursor_rejected_before_dispatch():
    dispatched = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched += 1
        return httpx.Response(
            200,
            json={"_embedded": {"collection": []}, "_links": {"next": {"href": "https://evil/x"}}},
        )

    calls: list[str] = []
    t = _tenant(httpx.MockTransport(handler), calls)
    p = t.list_page(None)
    with pytest.raises(ValueError):
        t.list_page(p.next_cursor)
    assert dispatched == 1


def test_read_content_uses_primary_and_bounds():
    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.endswith("/instances/file/invoice/v1"):
            return httpx.Response(200, json={"id": "v1", "chronicle_id": "c1", "type": "invoice"})
        if p.endswith("/instances/file/invoice/v1/contents"):
            return httpx.Response(
                200,
                json={
                    "_embedded": {
                        "collection": [
                            {
                                "rendition_type": "primary",
                                "source": "FILE_ID",
                                "blob_id": "b1",
                                "mime_type": "application/pdf",
                            }
                        ]
                    }
                },
            )
        if p.endswith("/v3/files/b1/stream"):
            return httpx.Response(200, content=b"abc")
        return httpx.Response(404)

    calls: list[str] = []
    t = _tenant(httpx.MockTransport(handler), calls)
    t._type_cache[("c1", "v1")] = "invoice"
    assert b"".join(t.read_content("c1", "v1")) == b"abc"


def test_get_blocks_prefers_text_secondary_and_extraction_method():
    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.endswith("/instances/file/invoice/v1"):
            return httpx.Response(200, json={"id": "v1", "chronicle_id": "c1", "type": "invoice"})
        if p.endswith("/instances/file/invoice/v1/contents"):
            return httpx.Response(
                200,
                json={
                    "_embedded": {
                        "collection": [
                            {
                                "rendition_type": "primary",
                                "source": "FILE_ID",
                                "blob_id": "b1",
                                "mime_type": "application/pdf",
                            },
                            {
                                "rendition_type": "secondary",
                                "source": "FILE_ID",
                                "blob_id": "t1",
                                "mime_type": "text/plain",
                            },
                        ]
                    }
                },
            )
        if p.endswith("/v3/files/t1/stream"):
            return httpx.Response(200, content=b"l1\nl2")
        return httpx.Response(404)

    calls: list[str] = []
    t = _tenant(httpx.MockTransport(handler), calls)
    t._type_cache[("c1", "v1")] = "invoice"
    blocks = t.get_blocks("c1", "v1")
    assert len(blocks) == 2
    assert t.extraction_method_for("c1", "v1").startswith("FILE_ID:t1")


def test_missing_plaintext_raises_capability_error_without_deleting_state():
    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.endswith("/instances/file/invoice/v1"):
            return httpx.Response(200, json={"id": "v1", "chronicle_id": "c1", "type": "invoice"})
        if p.endswith("/instances/file/invoice/v1/contents"):
            return httpx.Response(
                200,
                json={
                    "_embedded": {
                        "collection": [
                            {
                                "rendition_type": "primary",
                                "source": "FILE_ID",
                                "blob_id": "b1",
                                "mime_type": "application/pdf",
                            }
                        ]
                    }
                },
            )
        if p.endswith("/v3/files/b1/stream"):
            return httpx.Response(200, content=b"pdf")
        return httpx.Response(404)

    calls: list[str] = []
    t = _tenant(httpx.MockTransport(handler), calls)
    t._type_cache[("c1", "v1")] = "invoice"
    with pytest.raises(MissingExtractionCapability):
        t.get_blocks("c1", "v1")
    assert b"".join(t.read_content("c1", "v1")) == b"pdf"


def test_changed_chronicle_and_status_mapping_and_bounds():
    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.endswith("/instances/file/invoice/v1"):
            return httpx.Response(
                200, json={"id": "v1", "chronicle_id": "other", "type": "invoice"}
            )
        if p.endswith("/v3/files/x/stream"):
            return httpx.Response(403)
        return httpx.Response(401)

    calls: list[str] = []
    t = _tenant(httpx.MockTransport(handler), calls, max_item_bytes=2)
    t._type_cache[("c1", "v1")] = "invoice"
    with pytest.raises(OpenTextError, match="unstable_snapshot"):
        t.read_content("c1", "v1")


def test_client_ownership_close(monkeypatch: pytest.MonkeyPatch):
    closed = {"owned": 0, "supplied": 0}

    class TrackClient(httpx.Client):
        def close(self):
            closed["owned"] += 1
            super().close()

    monkeypatch.setattr("craft_data.opentext.httpx.Client", TrackClient)
    t1 = OpenTextTenant(
        cms_url="https://us.api.opentext.com/cms",
        css_url="https://css.opentext.com",
        root_type="t",
        root_id="r",
        mission="m",
        classification="c",
        token_provider=lambda: "x",
    )
    supplied = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})))
    orig_close = supplied.close

    def close2():
        closed["supplied"] += 1
        return orig_close()

    supplied.close = close2  # type: ignore[method-assign]
    t2 = OpenTextTenant(
        cms_url="https://us.api.opentext.com/cms",
        css_url="https://css.opentext.com",
        root_type="t",
        root_id="r",
        mission="m",
        classification="c",
        token_provider=lambda: "x",
        http_client=supplied,
    )
    t1.close()
    t2.close()
    assert closed["owned"] == 1
    assert closed["supplied"] == 0


def test_api_rendition_to_markdown_keeps_originals_external(tmp_path):
    from craft_data.models import StorageMode
    from craft_data.simulator import TenantReconciler
    from craft_data.store import DocumentStore

    listing = {
        "id": "v1",
        "chronicle_id": "logical-1",
        "name": "generated.pdf",
        "category": "file",
        "type": "report",
        "latest": True,
        "renditions": [
            {
                "source": "FILE_ID",
                "blob_id": "binary-1",
                "mime_type": "application/pdf",
                "rendition_type": "primary",
            },
            {
                "source": "FILE_ID",
                "blob_id": "text-1",
                "mime_type": "text/plain",
                "rendition_type": "secondary",
            },
        ],
    }

    def response(request):
        assert request.headers["Authorization"] == "Bearer token"
        path = request.url.path
        if path.endswith("/items"):
            return httpx.Response(200, json={"_embedded": {"collection": [listing]}, "_links": {}})
        if path.endswith("/instances/file/report/v1"):
            return httpx.Response(200, json=listing)
        if path.endswith("/v3/files/binary-1/stream"):
            return httpx.Response(200, content=b"%PDF generated original bytes")
        if path.endswith("/v3/files/text-1/stream"):
            return httpx.Response(200, content=b"Generated mass 12 kg.\nSecond line.")
        return httpx.Response(404)

    token_calls = []
    tenant = _tenant(httpx.MockTransport(response), token_calls)
    store = DocumentStore(tmp_path / "external", mode=StorageMode.EXTERNAL_ORIGINALS)
    try:
        result = TenantReconciler(store).scan(tenant)
        assert result.status == "complete"
        document = store.get_external_document("opentext", "logical-1")
        version = store.list_versions(document.document_id)[0]
        assert version.blob_path is None and version.extraction_method.startswith("FILE_ID:text-1")
        reference = store.refresh_reference(version.version_id)
        assert "Generated mass 12 kg." in reference.read_text()
        assert not list(store.root.rglob("*.bin"))
    finally:
        tenant.close()


def test_malformed_page_never_causes_reconciliation_deletion(tmp_path):
    from craft_data.models import StorageMode
    from craft_data.simulator import TenantReconciler
    from craft_data.store import DocumentStore

    tenant = _tenant(httpx.MockTransport(lambda request: httpx.Response(200, json={})), [])
    try:
        store = DocumentStore(tmp_path / "external", mode=StorageMode.EXTERNAL_ORIGINALS)
        result = TenantReconciler(store).scan(tenant)
        assert result.status == "partial" and result.changes == ()
    finally:
        tenant.close()


def test_version_pages_keep_specific_ids_and_reject_foreign_metadata():
    def response(request):
        if request.url.path.endswith("/versions"):
            return httpx.Response(
                200,
                json={
                    "_embedded": {
                        "collection": [
                            {"id": "v1", "chronicle_id": "c1"},
                            {"id": "v2", "chronicle_id": "c1"},
                        ]
                    },
                    "_links": {},
                },
            )
        if request.url.path.endswith("/instances/file/invoice/v2"):
            return httpx.Response(
                200, json={"id": "v2", "chronicle_id": "other", "type": "invoice"}
            )
        return httpx.Response(404)

    tenant = _tenant(httpx.MockTransport(response), [])
    tenant._type_cache[("c1", "v1")] = "invoice"
    try:
        assert tenant.list_versions("c1", "v1") == ("v1", "v2")
        with pytest.raises(OpenTextError, match="unstable_snapshot"):
            tenant.read_content("c1", "v2")
    finally:
        tenant.close()
