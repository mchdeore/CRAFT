import base64
from contextlib import contextmanager
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import httpx
import msgspec
import pytest
from pypdf import PdfWriter
from redis.exceptions import RedisError
from sqlalchemy import select

from app.infrastructure.database import metadata as m
from app.infrastructure.errors import Forbidden, NotFound, TooLarge
from app.library import enrichment, queue
from app.library.handlers import OcrJobRequest, recover_ocr
from app.library.index import build


@pytest.fixture
def ocr_document(library, monkeypatch):
    from app.infrastructure.provider.simulated import SimulatedProvider

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    output = BytesIO()
    writer.write(output)
    provider = SimulatedProvider(library["root"])
    provider.write_content("reference/ocr-source.pdf", output.getvalue())
    record = next(
        row for row in provider.items().values() if row["path"].endswith("ocr-source.pdf")
    )
    monkeypatch.setattr(
        enrichment, "enqueue_version_jobs", lambda settings, versions: len(versions)
    )
    build(library["settings"], enrich_ocr=True)
    return record["item"], 1


def test_extract_text_reads_pages_then_fallback() -> None:
    text, pages = enrichment._extract_text({"pages": [{"markdown": "First"}, {"text": "Second"}]})
    assert text == "First\n\nSecond"
    assert pages == 2

    fallback, fallback_pages = enrichment._extract_text({"content": "Synthetic fallback"})
    assert fallback == "Synthetic fallback"
    assert fallback_pages == 1


def test_retry_delay_remains_bounded_for_explicit_retries(settings) -> None:
    configured = msgspec.structs.replace(settings, ocr_retry_seconds=1)
    assert enrichment.retry_delay(configured, 1) == 1
    assert enrichment.retry_delay(configured, 3) == 4
    assert enrichment.retry_delay(configured, 1000000) == 86400

    fallback, fallback_pages = enrichment._extract_text({"pages": [], "text": "Synthetic fallback"})
    assert fallback == "Synthetic fallback"
    assert fallback_pages == 1


@pytest.mark.parametrize(
    "media_type,key", [("application/pdf", "document_url"), ("image/png", "image_url")]
)
def test_foundry_ocr_client_posts_expected_payload(settings, monkeypatch, media_type, key) -> None:
    configured = msgspec.structs.replace(
        settings,
        model_endpoint="https://models.craft.test",
        model_key="synthetic-key",
        model_ocr="mistral-ocr-4-0",
    )
    calls: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"pages": [{"markdown": "Page one"}, {"content": "Page two"}]}

    class FakeClient:
        def __init__(self, *, timeout: int, follow_redirects: bool, trust_env: bool):
            calls["init"] = {
                "timeout": timeout,
                "follow_redirects": follow_redirects,
                "trust_env": trust_env,
            }

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
            calls["url"] = url
            calls["headers"] = headers
            calls["json"] = json
            return FakeResponse()

    monkeypatch.setattr(enrichment.httpx, "Client", FakeClient)
    extracted = enrichment.FoundryOcrClient(configured).extract(
        content=b"Synthetic OCR payload", media_type=media_type
    )

    assert extracted.model == "mistral-ocr-4-0"
    assert extracted.text == "Page one\n\nPage two"
    assert extracted.page_count == 2
    assert calls["url"] == "https://models.craft.test/providers/mistral/azure/ocr"
    assert calls["headers"] == {"Authorization": "Bearer synthetic-key"}
    payload = calls["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "mistral-ocr-4-0"
    assert payload["document"] == {
        "type": key,
        key: "data:"
        + media_type
        + ";base64,"
        + base64.b64encode(b"Synthetic OCR payload").decode("ascii"),
    }


def test_run_ocr_job_skips_when_text_exists(settings, monkeypatch) -> None:
    monkeypatch.setattr(enrichment.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(enrichment, "_read_ocr_text", lambda *_: "already indexed")

    def should_not_run(*args, **kwargs):
        _ = (args, kwargs)
        raise AssertionError("ocr_version should not run when OCR text already exists")

    monkeypatch.setattr(enrichment, "ocr_version", should_not_run)
    assert enrichment.run_ocr_job(item="itm_abcdef123456", version=2) == {
        "item": "itm_abcdef123456",
        "version": 2,
        "status": "skipped",
    }


def test_run_ocr_job_keeps_ocr_error_when_policy_write_fails(settings, monkeypatch) -> None:
    class Unavailable:
        def hset(self, *args, **kwargs):
            raise RedisError("Synthetic Redis outage")

    job = SimpleNamespace(origin="craft-ocr", meta={}, connection=Unavailable())
    monkeypatch.setattr(enrichment.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(enrichment, "_read_ocr_text", lambda *_: None)
    monkeypatch.setattr(enrichment, "get_current_job", lambda: job)

    def rejected(*args, **kwargs):
        raise enrichment.OcrPermanentError("Synthetic rejected source")

    monkeypatch.setattr(enrichment, "ocr_version", rejected)
    with pytest.raises(enrichment.OcrPermanentError):
        enrichment.run_ocr_job(item="itm_abcdef123456", version=2)


def test_ocr_version_updates_doc_version_fields(library, ocr_document) -> None:
    item, version = ocr_document

    class FakeClient:
        def extract(self, *, content: bytes, media_type: str) -> enrichment.OcrExtraction:
            assert content
            assert media_type
            return enrichment.OcrExtraction(
                model="synthetic-ocr", text="Synthetic OCR text.", page_count=1
            )

    result = enrichment.ocr_version(library["settings"], item, version, client=FakeClient())
    assert result["item"] == item
    assert result["version"] == version
    assert result["model"] == "synthetic-ocr"
    assert result["page_count"] == 1
    assert result["characters"] == len("Synthetic OCR text.")

    with library["sessions"].begin() as session:
        row = session.execute(
            select(
                m.doc_versions.c.ocr_text,
                m.doc_versions.c.ocr_model,
                m.doc_versions.c.ocr_updated_at,
            ).where(m.doc_versions.c.item == item, m.doc_versions.c.version == version)
        ).one()
    assert row.ocr_text == "Synthetic OCR text."
    assert row.ocr_model == "synthetic-ocr"
    assert row.ocr_updated_at is not None


def test_ocr_source_limit_prevents_model_call(library, ocr_document):
    item, version = ocr_document
    configured = msgspec.structs.replace(library["settings"], ocr_max_bytes=8)

    class UnexpectedClient:
        def extract(self, **kwargs):
            raise AssertionError("Oversized source must not reach OCR")

    with pytest.raises(enrichment.OcrPermanentError, match="byte limit"):
        enrichment.ocr_version(configured, item, version, client=UnexpectedClient())


@pytest.mark.parametrize("mutation", ["delete", "hash", "remove"])
def test_ocr_revalidates_index_after_inference(library, ocr_document, mutation):
    item, version = ocr_document

    class ChangingClient:
        def extract(self, **kwargs):
            with library["sessions"].begin() as session:
                if mutation == "delete":
                    session.execute(
                        m.provider_items.update()
                        .where(m.provider_items.c.item == item)
                        .values(deleted_at=datetime.now(UTC))
                    )
                elif mutation == "hash":
                    session.execute(
                        m.doc_versions.update()
                        .where(m.doc_versions.c.item == item, m.doc_versions.c.version == version)
                        .values(sha256=b"x" * 32)
                    )
                else:
                    session.execute(m.doc_versions.delete().where(m.doc_versions.c.item == item))
            return enrichment.OcrExtraction(model="generated", text="Stale result", page_count=1)

    with pytest.raises(NotFound):
        enrichment.ocr_version(library["settings"], item, version, client=ChangingClient())
    with library["sessions"].begin() as session:
        assert (
            session.execute(
                select(m.doc_versions.c.ocr_text).where(m.doc_versions.c.item == item)
            ).scalar_one_or_none()
            is None
        )


@pytest.mark.parametrize("mutation", ["delete", "hash", "missing"])
def test_invalid_ocr_source_never_reaches_inference(library, ocr_document, mutation):
    item, version = ocr_document
    if mutation == "missing":
        (library["root"] / ".provider" / "versions" / item / str(version)).unlink()
    else:
        with library["sessions"].begin() as session:
            if mutation == "delete":
                session.execute(
                    m.provider_items.update()
                    .where(m.provider_items.c.item == item)
                    .values(deleted_at=datetime.now(UTC))
                )
            else:
                session.execute(
                    m.doc_versions.update()
                    .where(m.doc_versions.c.item == item)
                    .values(sha256=b"x" * 32)
                )

    class UnexpectedClient:
        def extract(self, **kwargs):
            raise AssertionError("Invalid source reached inference")

    with pytest.raises(NotFound):
        enrichment.ocr_version(library["settings"], item, version, client=UnexpectedClient())


def test_ocr_external_io_has_no_database_transaction(library, ocr_document, monkeypatch):
    item, version = ocr_document
    active = 0
    original = enrichment.unit_of_work

    @contextmanager
    def observed(*args, **kwargs):
        nonlocal active
        with original(*args, **kwargs) as session:
            active += 1
            try:
                yield session
            finally:
                active -= 1

    class Client:
        def extract(self, **kwargs):
            assert active == 0
            return enrichment.OcrExtraction(model="generated", text="Generated OCR", page_count=1)

    monkeypatch.setattr(enrichment, "unit_of_work", observed)
    enrichment.ocr_version(library["settings"], item, version, client=Client())


@pytest.mark.parametrize("policy", ["expired", "malformed"])
def test_operator_recovers_expired_or_malformed_ocr_policy(
    library, ocr_document, fake_queue, policy
):
    item, version = ocr_document
    settings = library["settings"]
    identity = f"craft:ocr:{settings.queue_name}:{item}:{version}"
    connection = queue.open_connection(settings)
    try:
        connection.set(identity, "expired-generated-job")
        if policy == "malformed":
            connection.hset(identity + ":policy", "attempts", "invalid")
        request = OcrJobRequest(item=item, version=version)
        first = recover_ocr(library["callers"]["admin"], request)
        repeated = recover_ocr(library["callers"]["admin"], request)
        assert (first.kind, first.item, first.version, first.force) == ("ocr", item, version, True)
        assert repeated.job_id == first.job_id
        assert connection.hget(identity + ":policy", "attempts") == b"1"
    finally:
        connection.close()


def test_operator_recovery_requires_admin(library, ocr_document, fake_queue):
    item, version = ocr_document
    with pytest.raises(Forbidden):
        recover_ocr(library["callers"]["demo"], OcrJobRequest(item=item, version=version))


@pytest.mark.parametrize("mutation", ["delete", "hash", "size"])
def test_operator_recovery_validates_source_before_queue_io(
    library, ocr_document, monkeypatch, mutation
):
    from dataclasses import replace

    from app.infrastructure.provider.simulated import SimulatedProvider

    item, version = ocr_document
    caller = library["callers"]["admin"]
    if mutation == "delete":
        SimulatedProvider(library["root"]).delete_item(item)
    elif mutation == "hash":
        (library["root"] / ".provider" / "versions" / item / str(version)).write_bytes(
            b"Generated changed snapshot"
        )
    else:
        caller = replace(caller, settings=msgspec.structs.replace(caller.settings, ocr_max_bytes=1))

    def unexpected(*args):
        raise AssertionError("Invalid recovery reached queue IO")

    monkeypatch.setattr(queue, "open_connection", unexpected)
    with pytest.raises(TooLarge if mutation == "size" else NotFound):
        recover_ocr(caller, OcrJobRequest(item=item, version=version))


def test_operator_recovery_releases_transaction_before_queue_io(
    library, ocr_document, fake_queue, monkeypatch
):
    item, version = ocr_document
    active = 0
    original_uow = enrichment.unit_of_work
    original_open = queue.open_connection

    @contextmanager
    def observed(*args, **kwargs):
        nonlocal active
        with original_uow(*args, **kwargs) as session:
            active += 1
            try:
                yield session
            finally:
                active -= 1

    def open_connection(settings):
        assert active == 0
        return original_open(settings)

    monkeypatch.setattr(enrichment, "unit_of_work", observed)
    monkeypatch.setattr(queue, "open_connection", open_connection)
    recover_ocr(library["callers"]["admin"], OcrJobRequest(item=item, version=version))


@pytest.mark.parametrize(
    "media_type,content", [("text/plain", b"Generated"), ("image/png", b"123456789")]
)
def test_ocr_client_rejects_source_before_http(settings, monkeypatch, media_type, content):
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid source must not open an HTTP client")

    monkeypatch.setattr(enrichment.httpx, "Client", unexpected)
    with pytest.raises(enrichment.OcrPermanentError):
        enrichment.FoundryOcrClient(msgspec.structs.replace(settings, ocr_max_bytes=8)).extract(
            content=content, media_type=media_type
        )


@pytest.mark.parametrize(
    "status,permanent", [(400, True), (401, True), (413, True), (429, False), (503, False)]
)
def test_ocr_http_failures_are_classified_without_response_content(
    settings, monkeypatch, status, permanent
):
    from app.infrastructure.errors import ApiError

    real_client = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, json={"generated": "failure"})
    )
    monkeypatch.setattr(
        enrichment.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    configured = msgspec.structs.replace(settings, model_endpoint="https://models.craft.test")
    with pytest.raises(ApiError) as raised:
        enrichment.FoundryOcrClient(configured).extract(
            content=b"Generated", media_type="application/pdf"
        )
    assert isinstance(raised.value, enrichment.OcrPermanentError) == permanent
    assert "generated" not in str(raised.value)
