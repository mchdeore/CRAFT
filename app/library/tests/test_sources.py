"""Citation checks, search and downloads answer from the live source, for readers only."""

import hashlib
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import msgspec
import pytest
from fastapi.testclient import TestClient

from app.infrastructure.access import Caller
from app.infrastructure.errors import InvalidInput, NotFound, NotSignedIn
from app.library.handlers import (
    ChunkRange,
    CitationCheck,
    LibrarySearch,
    OriginalRef,
    check_citation,
    download,
    read_chunks,
    search,
)
from app.tests.support import rows
from app.tests.support.cube import cube_source as cube_source

REVISED = b"# 1 Budget\n\n| Parameter | Value | Unit |\n|---|---|---|\n| Peak power | 450 | W |\n"


def budget_row(source):
    """The citation of the budget table's first row, as a cube value cites it."""
    page = read_chunks(
        source["caller"], ChunkRange(item=source["item"], version=1, from_block=1, to_block=1)
    )
    table = page.chunks[0]
    return msgspec.structs.replace(
        table.handle,
        locator=table.handle.locator + ";row=1",
        quote_sha256=hashlib.sha256(table.text.splitlines()[2].encode()).hexdigest(),
    )


def checked(caller, handle):
    result = check_citation(caller, CitationCheck(handle=handle))
    return result.status, result.latest_version


def stranger(source):
    return replace(source["caller"], user_id=uuid4())


def test_a_citation_check_answers_from_the_live_source(cube_source):
    source, caller = cube_source, cube_source["caller"]
    handle = budget_row(source)
    assert checked(caller, handle) == ("current", 1)
    other = hashlib.sha256(b"| Peak power | 401 | W |").hexdigest()
    assert checked(caller, msgspec.structs.replace(handle, quote_sha256=other)) == ("changed", 1)

    source["provider"].write_content(source["path"], REVISED)
    rows.load(source["owner"], source["provider"])
    assert checked(caller, handle) == ("newer_version", 2)

    source["provider"].delete_item(source["item"])
    assert checked(caller, handle) == ("withdrawn", 2)


def test_an_original_changed_in_place_is_changed_and_not_downloadable(cube_source):
    source = cube_source
    handle = budget_row(source)
    snapshot = Path(source["settings"].provider_root, ".provider/versions", source["item"], "1")
    snapshot.write_bytes(REVISED)

    assert checked(source["caller"], handle) == ("changed", 1)
    with pytest.raises(NotFound):
        download(source["caller"], OriginalRef(item=source["item"], version=1))


def test_citation_checks_need_a_reader_and_a_valid_handle(cube_source):
    handle = budget_row(cube_source)
    with pytest.raises(NotSignedIn):
        checked(Caller(), handle)
    with pytest.raises(NotFound):
        checked(stranger(cube_source), handle)
    with pytest.raises(InvalidInput):
        checked(cube_source["caller"], msgspec.structs.replace(handle, version=0))


def test_search_finds_current_passages_the_caller_may_read(cube_source):
    source, caller = cube_source, cube_source["caller"]
    source["provider"].write_content("library/handbook.md", b"# Handbook\n\nPeak power varies.\n")
    rows.load(source["owner"], source["provider"])
    handbook = next(k for k, r in source["provider"].items().items() if r["mission_key"] is None)
    query = LibrarySearch(query="peak power")

    def found(caller, request=query):
        return {(hit.handle.item, hit.handle.version) for hit in search(caller, request).hits}

    budget = source["item"]
    assert found(caller) == {(budget, 1), (handbook, 1)}
    # A document outside every mission is readable by everyone.
    assert found(stranger(source)) == {(handbook, 1)}
    in_mission = msgspec.structs.replace(query, missions=[source["mission"]])
    assert found(caller, in_mission) == {(budget, 1)}

    source["provider"].write_content(source["path"], REVISED)
    rows.load(source["owner"], source["provider"])
    assert found(caller) == {(budget, 2), (handbook, 1)}

    source["provider"].delete_item(budget)
    rows.load(source["owner"], source["provider"])
    assert found(caller) == {(handbook, 1)}


def test_download_returns_the_exact_original_to_a_reader(cube_source):
    source, item = cube_source, cube_source["item"]
    with pytest.raises(NotFound):
        download(stranger(source), OriginalRef(item=item, version=1))
    url = f"/api/library/documents/{item}/download"
    with TestClient(source["app"], base_url="https://testserver") as http:
        assert http.get(url, params={"version": 1}).status_code == 401
        source["sign_in"](http)
        response = http.get(url, params={"version": 1})
        assert response.status_code == 200
        assert response.content == source["provider"].read_content(item, 1)
        assert response.headers["content-disposition"] == 'attachment; filename="budget.md"'
        assert response.headers["cache-control"] == "no-store"
        assert http.get(url, params={"version": 2}).status_code == 404
        source["provider"].delete_item(item)
        assert http.get(url, params={"version": 1}).status_code == 404
