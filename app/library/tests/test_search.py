from collections import Counter

import msgspec
import pytest

from app.infrastructure.errors import InvalidInput, NotFound
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library import storage
from app.library.handlers import (
    ChunkRange,
    DocumentRef,
    ExploreInput,
    LibrarySearch,
    OriginalRef,
    download,
    explore,
    get_document,
    preview,
    read_chunks,
    search,
)
from app.library.index import build


def test_search_returns_handles_and_neighbours(library) -> None:
    hits = search(library["callers"]["demo"], LibrarySearch(query="radiator margin")).hits
    assert hits and hits[0].kind == "chunk" and hits[0].title == "thermal plan"
    assert hits[0].before is not None and hits[0].after is not None
    assert len(hits[0].handle.quote_sha256) == 64 and hits[0].handle.simulated


def test_search_spread(library) -> None:
    hits = search(
        library["callers"]["admin"], LibrarySearch(query="simulated", kinds=["chunk"])
    ).hits
    counts = Counter((h.handle.item, h.handle.version, h.heading_path) for h in hits)
    assert max(counts.values()) <= 2


def test_search_filters(library) -> None:
    caller = library["callers"]["admin"]
    for field, value in [
        ("missions", "msn-sim-01"),
        ("silos", "engineering"),
        ("stages", "st3-design"),
    ]:
        hits = search(
            caller, msgspec.convert({"query": "simulated", field: [value]}, LibrarySearch)
        ).hits
        assert hits
        attr = {"missions": "mission", "silos": "silo", "stages": "stage"}[field]
        assert all(getattr(h.tags, attr) == value for h in hits)
    assert all(
        h.kind == "document"
        for h in search(caller, LibrarySearch(query="simulated", kinds=["document"])).hits
    )


def test_read_and_explore_bounds(library) -> None:
    caller = library["callers"]["demo"]
    hit = search(caller, LibrarySearch(query="radiator margin")).hits[0]
    with pytest.raises(InvalidInput):
        read_chunks(caller, ChunkRange(item=hit.handle.item, version=1, from_block=1, to_block=21))
    with pytest.raises(InvalidInput):
        explore(caller, {"handle": hit.handle, "before": 11})
    result = explore(caller, ExploreInput(handle=hit.handle))
    assert all(
        c.handle.item == hit.handle.item and c.handle.version == hit.handle.version
        for c in result.chunks
    )


def test_preview_and_download(library) -> None:
    caller = library["callers"]["demo"]
    hit = search(caller, LibrarySearch(query="radiator margin")).hits[0]
    expected = SimulatedProvider(library["root"]).read_content(hit.handle.item, hit.handle.version)
    request = OriginalRef(item=hit.handle.item, version=hit.handle.version)
    a = preview(caller, request)
    b = download(caller, request)
    assert a.body == expected and b.body == expected
    assert a.headers["content-disposition"].startswith("attachment") and b.headers[
        "content-disposition"
    ].startswith("attachment")


def test_deleted_item_leaves_search(library) -> None:
    caller = library["callers"]["demo"]
    hit = search(caller, LibrarySearch(query="radiator margin")).hits[0]
    with library["sessions"].begin() as session:
        versions = [
            (row["version"], bytes(row["sha256"]))
            for row in storage.versions(session, hit.handle.item)
        ]
    assert versions
    SimulatedProvider(library["root"]).delete_item(hit.handle.item)
    build(library["settings"])
    assert not any(
        h.handle.item == hit.handle.item
        for h in search(caller, LibrarySearch(query="radiator margin")).hits
    )
    with pytest.raises(NotFound):
        get_document(caller, DocumentRef(item=hit.handle.item))
    with library["sessions"].begin() as session:
        item = storage.item(session, caller, hit.handle.item)
        assert item is not None and item["deleted_at"] is not None
        assert [
            (row["version"], bytes(row["sha256"]))
            for row in storage.versions(session, hit.handle.item)
        ] == versions


@pytest.mark.parametrize("change", ["delete", "move"])
def test_live_provider_removal_precedes_reindex(library, change) -> None:
    caller = library["callers"]["demo"]
    hit = search(caller, LibrarySearch(query="radiator margin")).hits[0]
    provider = SimulatedProvider(library["root"])
    if change == "delete":
        provider.delete_item(hit.handle.item)
    else:
        record = provider.get_item(hit.handle.item)
        destination = provider.path("missions/msn-withheld/engineering/st3-design/moved.md")
        destination.parent.mkdir(parents=True)
        provider.path(record["path"]).rename(destination)
        provider.scan()
        assert provider.get_item(hit.handle.item)["mission_key"] == "msn-withheld"
    # Cached metadata still describes the old readable source.
    found = search(caller, LibrarySearch(query="radiator margin"))
    assert all(row.handle.item != hit.handle.item for row in found.hits)


def test_plain_preview_is_sandboxed(library) -> None:
    provider = SimulatedProvider(library["root"])
    provider.write_content("reference/plain.txt", b"Simulated preview.")
    build(library["settings"])
    item = next(i["item"] for i in provider.items().values() if i["path"] == "reference/plain.txt")
    response = preview(library["callers"]["restricted"], OriginalRef(item=item, version=1))
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_native_shape_limits_are_checked(library) -> None:
    with pytest.raises(InvalidInput):
        search(library["callers"]["demo"], LibrarySearch(query="x" * 501))
    with pytest.raises(InvalidInput):
        search(library["callers"]["demo"], LibrarySearch(query="simulated", limit=9))


def test_candidate_budget_is_global(library) -> None:
    from app.library import storage
    from app.library.embed import embed

    provider = SimulatedProvider(library["root"])
    source = "\n".join(f"# {n} Section\n\nSimulated budget verification." for n in range(70))
    provider.write_content("reference/budget.md", source.encode())
    build(library["settings"])
    with library["sessions"].begin() as session:
        candidates = storage.candidates(
            session,
            library["callers"]["admin"],
            LibrarySearch(query="simulated"),
            embed("simulated"),
        )
    counts = Counter(method for _, method, _ in candidates)
    assert counts["lexical"] == 50 and counts["semantic"] == 50
