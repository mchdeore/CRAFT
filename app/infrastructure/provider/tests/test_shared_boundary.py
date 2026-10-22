import copy
import shutil
from io import BytesIO
from pathlib import Path

import msgspec
import pytest
from pypdf import PdfWriter

from app.cube import state
from app.infrastructure.provider import factory
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library import enrichment, service
from app.library.handlers import CitationCheck, LibrarySearch, OriginalRef
from app.library.index import build
from app.tests.support.library import library_context


class MemoryProvider:
    """Generated source bytes live only in memory; cached metadata stays independent."""

    def __init__(self, provider: SimulatedProvider):
        self.root = provider.root
        self.records = copy.deepcopy(provider.items())
        self.originals = {
            (item, version["version"]): provider.read_content(item, version["version"])
            for item, record in self.records.items()
            for version in record["versions"]
        }
        self.calls: list[str] = []

    def scan(self) -> list[dict]:
        self.calls.append("scan")
        return []

    def items(self) -> dict:
        self.calls.append("items")
        return copy.deepcopy(self.records)

    def list_changes(self, cursor: int) -> tuple[list[dict], int]:
        self.calls.append("changes")
        changes = [
            {"cursor": index, "kind": "created", "item": item}
            for index, item in enumerate(sorted(self.records), 1)
            if index > cursor
        ]
        return changes, len(self.records)

    def get_item(self, item: str) -> dict:
        self.calls.append("item")
        return copy.deepcopy(self.records[item])

    def read_content(self, item: str, version: int, *, max_bytes: int | None = None) -> bytes:
        self.calls.append("content")
        content = self.originals[item, version]
        if max_bytes is not None and len(content) > max_bytes:
            raise ValueError("Source exceeds configured byte limit.")
        return content

    def path(self, relative: str) -> Path:
        raise ValueError("source_is_read_only")

    def write_content(self, path: str, content: bytes) -> None:
        raise ValueError("source_is_read_only")

    def delete_item(self, item: str) -> None:
        raise ValueError("source_is_read_only")


def install(monkeypatch, root: Path) -> MemoryProvider:
    provider = MemoryProvider(SimulatedProvider(root))

    def selected(path, *, clock=None):
        assert Path(path).resolve() == provider.root
        return provider

    monkeypatch.setattr(factory, "SimulatedProvider", selected)
    shutil.rmtree(root / ".provider" / "versions")
    for record in provider.records.values():
        original = root / record["path"]
        assert original.resolve().is_relative_to(root.resolve())
        original.unlink(missing_ok=True)
    return provider


def test_shared_provider_index_original_citation_and_cube_sources(settings, tmp_path, monkeypatch):
    with library_context(settings, tmp_path) as library:
        provider = install(monkeypatch, tmp_path)
        build(library["settings"], rebuild=True)
        assert {"scan", "items", "changes", "content"} <= set(provider.calls)
        caller = library["callers"]["admin"]
        hits = service.search(caller, LibrarySearch(query="simulated")).hits
        assert hits
        handle = next(hit.handle for hit in hits if hit.kind == "chunk")
        original = OriginalRef(item=handle.item, version=handle.version)
        for attachment in (False, True):
            response = service.original(caller, original, attachment)
            assert response.body == provider.originals[handle.item, handle.version]
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
        assert service.check_citation(caller, CitationCheck(handle=handle)).status == "current"
        provider.records[handle.item]["deleted_at"] = "2026-10-17T12:00:00+00:00"
        assert service.check_citation(caller, CitationCheck(handle=handle)).status == "withdrawn"
        assert state.sources(caller)[handle.item]["deleted_at"] is not None
        assert SimulatedProvider(tmp_path).items()[handle.item]["deleted_at"] is None
        assert not (tmp_path / ".provider" / "versions").exists()
        assert all(not (tmp_path / record["path"]).exists() for record in provider.records.values())


def test_shared_provider_supports_bounded_ocr(settings, tmp_path, monkeypatch):
    with library_context(settings, tmp_path) as library:
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        output = BytesIO()
        writer.write(output)
        source = SimulatedProvider(tmp_path)
        source.write_content("reference/generated-ocr.pdf", output.getvalue())
        record = next(
            row for row in source.items().values() if row["path"].endswith("generated-ocr.pdf")
        )
        monkeypatch.setattr(
            enrichment, "enqueue_version_jobs", lambda settings, versions: len(versions)
        )
        build(library["settings"], enrich_ocr=True)
        provider = install(monkeypatch, tmp_path)

        class Client:
            def extract(self, *, content, media_type):
                assert content == output.getvalue()
                assert media_type == "application/pdf"
                return enrichment.OcrExtraction("generated-ocr", "Generated OCR text.", 1)

        enrichment.ocr_version(library["settings"], record["item"], 1, client=Client())
        assert "content" in provider.calls
        limited = msgspec.structs.replace(library["settings"], ocr_max_bytes=8)
        with pytest.raises(enrichment.OcrPermanentError, match="byte limit"):
            enrichment.ocr_version(limited, record["item"], 1, client=Client())


def test_provider_failure_never_falls_back_to_existing_original(settings, tmp_path, monkeypatch):
    with library_context(settings, tmp_path) as library:
        caller = library["callers"]["admin"]
        hit = service.search(caller, LibrarySearch(query="simulated")).hits[0]

        def refuse(*args, **kwargs):
            raise ValueError("generated_provider_unavailable")

        monkeypatch.setattr(factory, "SimulatedProvider", refuse)
        with pytest.raises(ValueError, match="^generated_provider_unavailable$"):
            service.original(
                caller, OriginalRef(item=hit.handle.item, version=hit.handle.version), True
            )
        with pytest.raises(ValueError, match="^generated_provider_unavailable$"):
            state.sources(caller)
