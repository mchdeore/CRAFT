"""Document inventory lists bounded, currently readable indexed metadata only."""

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import msgspec
import pytest

from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.database.unit_of_work import has_ambient_transaction
from app.infrastructure.errors import NotSignedIn
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library import handlers, storage
from app.library.handlers import DocumentRef, get_document, index_provider


def catalog(caller: Caller, limit: int | None = None) -> handlers.DocumentCatalog:
    operation = getattr(handlers, "list_documents", None)
    request = getattr(handlers, "DocumentList", None)
    assert callable(operation), "Missing authenticated list_documents operation"
    assert request is not None, "Missing bounded DocumentList request"
    return cast(
        handlers.DocumentCatalog,
        operation(caller, request() if limit is None else request(limit=limit)),
    )


@pytest.fixture
def catalog_source(library):
    caller = library["callers"]["demo"]
    assert "admin" not in caller.roles
    with library["sessions"].begin() as session:
        grants = [
            dict(row)
            for row in session.execute(
                m.mission_grants.select().where(m.mission_grants.c.user_id == caller.user_id)
            ).mappings()
        ]
        session.execute(
            m.mission_grants.delete().where(m.mission_grants.c.user_id == caller.user_id)
        )
        session.execute(
            m.mission_grants.insert().values(
                user_id=caller.user_id, mission_key="msn-sim-01", silo="engineering"
            )
        )
        assert not storage.provider_readable(
            session, caller, {"mission_key": "msn-sim-01", "silo": "budget"}
        )
    try:
        provider = SimulatedProvider(library["root"])
        path = f"missions/msn-sim-01/engineering/st3-design/catalog-{uuid4().hex}.md"
        provider.write_content(path, b"# Generated catalog source\n\nReadable catalog evidence.")
        item = next(key for key, row in provider.items().items() if row["path"] == path)
        index_provider(library["settings"])
        yield library, provider, item, path
    finally:
        with library["sessions"].begin() as session:
            session.execute(
                m.mission_grants.delete().where(m.mission_grants.c.user_id == caller.user_id)
            )
            if grants:
                session.execute(m.mission_grants.insert(), grants)


def test_catalog_returns_only_safe_current_metadata(catalog_source):
    library, _, item, _ = catalog_source
    result = catalog(library["callers"]["demo"])
    assert item in {row.item for row in result.documents}
    assert len(result.documents) <= 25 and isinstance(result.truncated, bool)
    for row in result.documents:
        checked = get_document(library["callers"]["demo"], DocumentRef(item=row.item))
        assert (row.version, row.classification, row.simulated) == (
            checked.version,
            checked.classification,
            checked.simulated,
        )
        assert row.title and len(row.title) <= 200
        assert set(msgspec.to_builtins(row)) == {
            "item",
            "title",
            "version",
            "classification",
            "simulated",
        }
    assert set(msgspec.to_builtins(result)) == {"documents", "truncated"}


def test_catalog_uses_readable_grants(catalog_source):
    library, _, item, _ = catalog_source
    assert item not in {row.item for row in catalog(library["callers"]["restricted"]).documents}
    assert item in {row.item for row in catalog(library["callers"]["admin"]).documents}
    for row in catalog(library["callers"]["restricted"]).documents:
        assert get_document(library["callers"]["restricted"], DocumentRef(item=row.item))


@pytest.mark.parametrize(
    "change",
    [
        "withdrawn",
        "permission",
        "missing_item",
        "missing_version",
        "wrong_sha",
        "new_version",
        "unindexed",
        "cached_deleted",
    ],
)
def test_catalog_excludes_stale_withdrawn_or_inaccessible_sources(catalog_source, change):
    library, provider, item, path = catalog_source
    if change == "withdrawn":
        provider.delete_item(item)
    elif change == "permission":
        destination = provider.path(path.replace("/engineering/", "/budget/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        provider.path(path).rename(destination)
        provider.scan()
    elif change == "new_version":
        provider.write_content(path, b"# Changed current source\n\nNot indexed yet.")
    elif change in {"unindexed", "cached_deleted"}:
        with library["sessions"].begin() as session:
            if change == "unindexed":
                storage.clear_index(session, [item])
            else:
                session.execute(
                    m.provider_items.update()
                    .where(m.provider_items.c.item == item)
                    .values(deleted_at=datetime.now(UTC))
                )
    else:
        records = provider.items()
        if change == "missing_item":
            del records[item]
        elif change == "missing_version":
            records[item]["versions"] = []
        else:
            records[item]["versions"][-1]["sha256"] = "0" * 64
        provider.save(records)
    assert item not in {row.item for row in catalog(library["callers"]["demo"]).documents}


def test_catalog_rechecks_permissions_before_emitting_cached_title(catalog_source, monkeypatch):
    library, _, item, _ = catalog_source
    original = SimulatedProvider.get_item
    examined = []

    def changed_current_metadata(provider, key):
        assert not has_ambient_transaction(), "Provider metadata fetched inside a UOW"
        record = original(provider, key)
        examined.append(key)
        if key == item:
            record = dict(record, mission_key="msn-sim-01", silo="budget")
        return record

    monkeypatch.setattr(SimulatedProvider, "get_item", changed_current_metadata)
    result = catalog(library["callers"]["demo"])
    assert item in examined and item not in {row.item for row in result.documents}


def test_catalog_checks_revoked_database_grants(catalog_source):
    library, _, item, _ = catalog_source
    caller = library["callers"]["demo"]
    with library["sessions"].begin() as session:
        grants = [
            dict(row)
            for row in session.execute(
                m.mission_grants.select().where(m.mission_grants.c.user_id == caller.user_id)
            ).mappings()
        ]
        session.execute(
            m.mission_grants.delete().where(m.mission_grants.c.user_id == caller.user_id)
        )
    try:
        assert item not in {row.item for row in catalog(caller).documents}
    finally:
        if grants:
            with library["sessions"].begin() as session:
                session.execute(m.mission_grants.insert(), grants)


def test_catalog_bounds_scan_results_and_fetches_metadata_outside_uow(library, monkeypatch):
    provider = SimulatedProvider(library["root"])
    for index in range(205):
        provider.write_content(
            f"missions/msn-sim-01/engineering/st3-design/catalog-bounded-{index:03}.md",
            f"# Generated source {index}\n\nBounded indexed evidence {index}.".encode(),
        )
    index_provider(library["settings"])
    original = SimulatedProvider.get_item
    examined = []

    def checked_metadata(provider, key):
        assert not has_ambient_transaction(), "Provider metadata fetched inside a UOW"
        examined.append(key)
        return original(provider, key)

    monkeypatch.setattr(SimulatedProvider, "get_item", checked_metadata)
    result = catalog(library["callers"]["admin"], 50)
    assert len(result.documents) == 50 and result.truncated is True
    assert 50 <= len(examined) <= 200
    assert len({row.item for row in result.documents}) == 50
    examined.clear()
    default = catalog(library["callers"]["admin"])
    assert len(default.documents) == 25 and default.truncated is True
    assert 25 <= len(examined) <= 200
    examined.clear()

    def withdrawn_metadata(provider, key):
        return dict(checked_metadata(provider, key), deleted_at="generated-withdrawal")

    monkeypatch.setattr(SimulatedProvider, "get_item", withdrawn_metadata)
    withdrawn = catalog(library["callers"]["admin"], 50)
    assert withdrawn.documents == [] and withdrawn.truncated is True
    assert len(examined) == 200


def test_catalog_titles_are_bounded_current_metadata(catalog_source):
    library, provider, item, _ = catalog_source
    records = provider.items()
    records[item]["title"] = "Current checked title " + "x" * 500 + "\n\x00hidden"
    provider.save(records)
    row = next(row for row in catalog(library["callers"]["demo"]).documents if row.item == item)
    assert row.title.startswith("Current checked title ") and len(row.title) <= 200
    assert not any(ord(char) < 32 or ord(char) == 127 for char in row.title)


def test_catalog_rejects_unsigned_callers_and_invalid_limits():
    with pytest.raises(NotSignedIn):
        catalog(Caller())
    request = getattr(handlers, "DocumentList", None)
    assert request is not None, "Missing bounded DocumentList request"
    for invalid in [0, 51, -1, "25"]:
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({"limit": invalid}, request, strict=True)
