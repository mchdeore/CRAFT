"""Unindexed withdrawal and access changes must close every source-read path."""

import secrets
from collections.abc import Callable
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.accounts import storage as accounts
from app.accounts.handlers import GrantMission, NewUser, add, grant
from app.infrastructure.database import metadata as m
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import NotFound
from app.infrastructure.mcp.stdio import local_caller
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library import storage
from app.library.handlers import (
    ChunkRange,
    CitationCheck,
    CompareVersions,
    DocumentRef,
    ExploreInput,
    FacetRequest,
    LibrarySearch,
    OriginalRef,
    check_citation,
    compare_versions,
    download,
    explore,
    get_document,
    index_provider,
    preview,
    read_chunks,
    read_facets,
    search,
)


@pytest.fixture
def fresh_source(library):
    provider = SimulatedProvider(library["root"])
    username = "fresh_" + uuid4().hex
    password = secrets.token_urlsafe(24)
    path = f"missions/msn-sim-01/engineering/st3-design/{username}.md"
    caller = None
    item = None
    try:
        add(
            library["callers"]["admin"],
            NewUser(username=username, display_name="Generated source reader", password=password),
        )
        grant(
            library["callers"]["admin"],
            GrantMission(username=username, mission="msn-sim-01", silo="engineering"),
        )
        caller = replace(local_caller(username, library["settings"]), origin="python")
        provider.write_content(path, b"# Generated engineering\n\nOriginal simulated requirement.")
        item = next(key for key, row in provider.items().items() if row["path"] == path)
        index_provider(library["settings"])
        provider.write_content(path, b"# Generated engineering\n\nRevised simulated requirement.")
        index_provider(library["settings"])
        handle = (
            read_chunks(caller, ChunkRange(item=item, version=1, from_block=1, to_block=1))
            .chunks[0]
            .handle
        )
        reads: dict[str, Callable[[], object]] = {
            "document": lambda: get_document(caller, DocumentRef(item=item, version=1)),
            "chunks": lambda: read_chunks(
                caller, ChunkRange(item=item, version=1, from_block=1, to_block=1)
            ),
            "explore": lambda: explore(caller, ExploreInput(handle=handle)),
            "compare": lambda: compare_versions(
                caller, CompareVersions(item=item, from_version=1, to_version=2)
            ),
            "preview": lambda: preview(caller, OriginalRef(item=item, version=1)),
            "download": lambda: download(caller, OriginalRef(item=item, version=1)),
        }

        def sign_in(client):
            return client.post(
                "/api/session",
                headers={"Origin": "http://localhost:8000"},
                json={"username": username, "password": password},
            )

        yield dict(library, reader=caller, sign_in=sign_in), provider, item, path, handle, reads
    finally:
        with library["sessions"].begin() as session:
            if item is not None:
                storage.clear_index(session, [item])
                session.execute(m.provider_items.delete().where(m.provider_items.c.item == item))
            user = session.execute(
                select(accounts.users.c.id).where(accounts.users.c.username == username)
            ).scalar_one_or_none()
            if user is not None:
                session.execute(m.mission_grants.delete().where(m.mission_grants.c.user_id == user))
                for table in [
                    accounts.auth_sessions,
                    accounts.local_credentials,
                    accounts.user_roles,
                ]:
                    session.execute(table.delete().where(table.c.user_id == user))
                session.execute(accounts.users.delete().where(accounts.users.c.id == user))
        if caller is not None and caller.sessions is not None:
            caller.sessions.kw["bind"].dispose()


@pytest.mark.parametrize(
    "surface", ["document", "chunks", "explore", "compare", "preview", "download"]
)
@pytest.mark.parametrize("change", ["withdrawn", "permission", "missing_item", "missing_version"])
def test_unindexed_source_changes_close_reads(fresh_source, surface, change):
    _, provider, item, path, _, reads = fresh_source
    if change == "withdrawn":
        provider.delete_item(item)
    elif change == "permission":
        destination = provider.path(path.replace("/engineering/", "/budget/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        provider.path(path).rename(destination)
        provider.scan()
    else:
        records = provider.items()
        if change == "missing_item":
            del records[item]
        else:
            records[item]["versions"] = [
                row for row in records[item]["versions"] if row["version"] != 1
            ]
        provider.save(records)
    with pytest.raises(NotFound):
        reads[surface]()


@pytest.mark.parametrize(
    "surface", ["document", "chunks", "explore", "compare", "preview", "download"]
)
def test_active_source_keeps_explicit_historical_reads(fresh_source, surface):
    _, _, _, _, _, reads = fresh_source
    assert reads[surface]() is not None


def test_default_document_keeps_available_latest_indexed_version(fresh_source):
    library, provider, item, path, _, _ = fresh_source
    provider.write_content(path, b"# Generated engineering\n\nUnindexed simulated requirement.")
    document = get_document(library["reader"], DocumentRef(item=item))
    assert document.version == 2


@pytest.mark.parametrize("surface", ["preview", "download"])
def test_missing_snapshot_is_safe_not_found(fresh_source, surface):
    _, provider, item, _, _, reads = fresh_source
    (provider.meta / "versions" / item / "1").unlink()
    with pytest.raises(NotFound):
        reads[surface]()


def test_withdrawn_citation_reports_status_without_serving_source(fresh_source):
    library, provider, item, _, handle, _ = fresh_source
    provider.delete_item(item)
    assert check_citation(library["reader"], CitationCheck(handle=handle)).status == "withdrawn"


@pytest.mark.parametrize(
    "surface", ["document", "chunks", "explore", "compare", "preview", "download"]
)
@pytest.mark.parametrize("during_provider_read", [False, True])
def test_current_indexed_grants_cannot_be_replaced_by_public_source(
    fresh_source, surface, during_provider_read, monkeypatch
):
    library, provider, _, path, _, reads = fresh_source
    caller = library["reader"]
    sessions = library["sessions"]
    destination = provider.path("reference/fresh-read.md")
    destination.parent.mkdir(parents=True, exist_ok=True)
    provider.path(path).rename(destination)
    provider.scan()
    with sessions.begin() as session:
        grants = [
            dict(row)
            for row in session.execute(
                select(m.mission_grants).where(m.mission_grants.c.user_id == caller.user_id)
            )
            .mappings()
            .all()
        ]
    assert grants

    def revoke():
        with sessions.begin() as session:
            session.execute(
                m.mission_grants.delete().where(m.mission_grants.c.user_id == caller.user_id)
            )

    original = SimulatedProvider.get_item

    def revoke_on_read(self, key):
        result = original(self, key)
        revoke()
        return result

    try:
        if during_provider_read:
            monkeypatch.setattr(SimulatedProvider, "get_item", revoke_on_read)
        else:
            revoke()
        with pytest.raises(NotFound):
            reads[surface]()
    finally:
        revoke()
        with sessions.begin() as session:
            session.execute(m.mission_grants.insert(), grants)


@pytest.mark.parametrize(
    "surface", ["document", "chunks", "explore", "compare", "preview", "download"]
)
def test_provider_io_runs_outside_database_transactions(fresh_source, surface, monkeypatch):
    _, _, _, _, _, reads = fresh_source
    active = set()
    seen = []

    def started(session, transaction):
        active.add(transaction)

    def ended(session, transaction):
        active.discard(transaction)

    original_item = SimulatedProvider.get_item
    original_content = SimulatedProvider.read_content

    def inspected_item(self, key):
        assert not active
        seen.append("metadata")
        return original_item(self, key)

    def inspected_content(self, key, version):
        assert not active
        seen.append("content")
        return original_content(self, key, version)

    monkeypatch.setattr(SimulatedProvider, "get_item", inspected_item)
    monkeypatch.setattr(SimulatedProvider, "read_content", inspected_content)
    event.listen(Session, "after_transaction_create", started)
    event.listen(Session, "after_transaction_end", ended)
    try:
        assert reads[surface]() is not None
    finally:
        event.remove(Session, "after_transaction_create", started)
        event.remove(Session, "after_transaction_end", ended)
    assert "metadata" in seen
    assert ("content" in seen) == (surface in {"preview", "download"})


@pytest.mark.parametrize(
    "surface",
    [
        "document",
        "chunks",
        "explore",
        "compare",
        "preview",
        "download",
        "citation",
        "search",
        "facets",
    ],
)
def test_ambient_transaction_refuses_provider_io(fresh_source, surface, monkeypatch):
    library, _, _, _, handle, reads = fresh_source
    caller = library["reader"]
    reads = reads | {
        "citation": lambda: check_citation(caller, CitationCheck(handle=handle)),
        "search": lambda: search(caller, LibrarySearch(query="generated requirement")),
        "facets": lambda: read_facets(
            caller, FacetRequest(missions=["msn-sim-01"], facets=["design"])
        ),
    }
    seen = []

    def unexpected(*args, **kwargs):
        seen.append(True)
        raise AssertionError("provider I/O under ambient database transaction")

    with monkeypatch.context() as patch:
        for method in ("get_item", "read_content", "items"):
            patch.setattr(SimulatedProvider, method, unexpected)
        with unit_of_work(caller.sessions, read_only=True):
            with pytest.raises(NotFound):
                reads[surface]()
    assert not seen


@pytest.mark.parametrize(
    "surface", ["document", "chunks", "explore", "compare", "preview", "download"]
)
@pytest.mark.parametrize("fingerprint", ["mismatched", "missing", "malformed"])
def test_live_version_fingerprint_must_match_index(fresh_source, surface, fingerprint):
    _, provider, item, _, _, reads = fresh_source
    records = provider.items()
    version = records[item]["versions"][0]
    if fingerprint == "missing":
        del version["sha256"]
    else:
        version["sha256"] = [] if fingerprint == "malformed" else "0" * 64
    provider.save(records)
    with pytest.raises(NotFound):
        reads[surface]()


@pytest.mark.parametrize("change", ["missing_version", "mismatched_fingerprint"])
def test_compare_checks_the_second_live_version(fresh_source, change):
    _, provider, item, _, _, reads = fresh_source
    records = provider.items()
    if change == "missing_version":
        records[item]["versions"] = [
            version for version in records[item]["versions"] if version["version"] != 2
        ]
    else:
        records[item]["versions"][1]["sha256"] = "0" * 64
    provider.save(records)
    with pytest.raises(NotFound):
        reads["compare"]()


@pytest.mark.parametrize("surface", ["preview", "download"])
def test_corrupted_original_snapshot_is_safe_not_found(fresh_source, surface):
    _, provider, item, _, _, reads = fresh_source
    (provider.meta / "versions" / item / "1").write_bytes(b"Altered generated snapshot.")
    with pytest.raises(NotFound):
        reads[surface]()


@pytest.mark.parametrize("surface", ["preview", "download"])
@pytest.mark.parametrize("change", ["grant", "withdrawn"])
def test_original_rechecks_access_after_fetch(fresh_source, surface, change, monkeypatch):
    library, provider, _, _, _, reads = fresh_source
    caller = library["reader"]
    sessions = library["sessions"]
    with sessions.begin() as session:
        grants = [
            dict(row)
            for row in session.execute(
                select(m.mission_grants).where(m.mission_grants.c.user_id == caller.user_id)
            )
            .mappings()
            .all()
        ]

    def revoke():
        with sessions.begin() as session:
            session.execute(
                m.mission_grants.delete().where(m.mission_grants.c.user_id == caller.user_id)
            )

    original = SimulatedProvider.read_content
    fetched = []

    def changed_on_fetch(self, key, version):
        content = original(self, key, version)
        fetched.append(True)
        if change == "grant":
            revoke()
        else:
            provider.delete_item(key)
        return content

    monkeypatch.setattr(SimulatedProvider, "read_content", changed_on_fetch)
    try:
        with pytest.raises(NotFound):
            reads[surface]()
        assert fetched == [True]
    finally:
        if change == "grant":
            revoke()
            with sessions.begin() as session:
                session.execute(m.mission_grants.insert(), grants)


def test_protected_http_metadata_and_errors_are_not_stored(fresh_source):
    from fastapi.testclient import TestClient

    from app import create_app

    library, provider, item, _, _, _ = fresh_source
    with TestClient(create_app(library["settings"]), base_url="https://testserver") as client:
        signed = library["sign_in"](client)
        assert signed.status_code == 200
        cache_policy = signed.headers.get("Cache-Control")
        assert cache_policy == "no-store"
        route = f"/api/library/documents/{item}?version=1"
        readable = client.get(route)
        assert readable.status_code == 200
        cache_policy = readable.headers.get("Cache-Control")
        assert cache_policy == "no-store"
        provider.delete_item(item)
        refused = client.get(route)
        assert refused.status_code == 404
        cache_policy = refused.headers.get("Cache-Control")
        assert cache_policy == "no-store"
    with TestClient(create_app(library["settings"]), base_url="https://testserver") as client:
        unsigned = client.get(route)
        assert unsigned.status_code == 401
        cache_policy = unsigned.headers.get("Cache-Control")
        assert cache_policy == "no-store"
