"""Real provider bytes, persisted differences, row evidence and readable version checks."""

import hashlib
from collections import Counter
from dataclasses import replace

import msgspec
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import InvalidInput, NotFound
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library import storage
from app.library.diff import compare
from app.library.handlers import (
    ChunkRange,
    CitationCheck,
    CompareVersions,
    check_citation,
    compare_versions,
    index_provider,
    read_chunks,
)
from app.tests.support import model as scripted_model
from app.tests.support.demo import demo as demo


def hashes(values):
    return {index: hashlib.sha256(value.encode()).digest() for index, value in enumerate(values, 1)}


def markdown(values):
    return "\n\n".join(
        f"# {index} Section\n\nSimulated block {value}." for index, value in enumerate(values, 1)
    ).encode()


def versions(library, count=8):
    provider = SimulatedProvider(library["root"])
    path = "reference/versions.md"
    initial = [str(index) for index in range(count)]
    provider.write_content(path, markdown(initial))
    item = next(item for item, row in provider.items().items() if row["path"] == path)
    index_provider(library["settings"])
    provider.write_content(path, markdown(["changed-" + value for value in initial]))
    index_provider(library["settings"])
    provider.write_content(path, markdown(["third-" + value for value in initial]))
    index_provider(library["settings"])
    return provider, item


def test_diff_labels():
    old = hashes(["a", "b", "c", "d", "e", "f", "g", "h"])
    new = hashes(["a", "x", "d", "c", "g", "f", "b", "z"])
    result = compare(old, new)
    assert Counter(row["change"] for row in result) == {
        "moved": 4,
        "added": 1,
        "removed": 1,
        "changed": 1,
    }
    assert compare(hashes(["repeat", "repeat", "end"]), hashes(["repeat", "end", "repeat"])) == [
        {"change": "moved", "from_block": 2, "to_block": 3},
        {"change": "moved", "from_block": 3, "to_block": 2},
    ]
    assert compare(old, old) == []


def test_diff_stored_on_index(library):
    _, item = versions(library)
    result = compare_versions(
        library["callers"]["admin"], CompareVersions(item=item, from_version=1, to_version=2)
    )
    with library["sessions"].begin() as session:
        stored = session.execute(
            select(
                m.version_diffs.c.change, m.version_diffs.c.from_block, m.version_diffs.c.to_block
            )
            .join(m.doc_versions, m.doc_versions.c.id == m.version_diffs.c.from_version)
            .where(m.doc_versions.c.item == item, m.doc_versions.c.version == 1)
            .order_by(m.version_diffs.c.seq)
        ).all()
    assert len(stored) == len(result.blocks) == 8
    assert all(row.change == "changed" for row in stored)
    assert index_provider(library["settings"]) == {}


@pytest.mark.slow
def test_compare_any_pair(library):
    _, item = versions(library, 600)
    caller = library["callers"]["admin"]
    for before, after in [(1, 3), (3, 1), (1, 2)]:
        result = compare_versions(
            caller, CompareVersions(item=item, from_version=before, to_version=after)
        )
        assert len(result.blocks) == 500 and result.truncated
    same = compare_versions(caller, CompareVersions(item=item, from_version=1, to_version=1))
    assert not same.blocks and not same.truncated
    with pytest.raises(NotFound):
        compare_versions(caller, CompareVersions(item=item, from_version=1, to_version=99))
    with pytest.raises(InvalidInput):
        compare_versions(caller, CompareVersions(item=item, from_version=0, to_version=1))


def one_handle(library, item, version=1, block=1):
    return (
        read_chunks(
            library["callers"]["admin"],
            ChunkRange(item=item, version=version, from_block=block, to_block=block),
        )
        .chunks[0]
        .handle
    )


def test_check_citation_states(library):
    provider, item = versions(library)
    caller = library["callers"]["admin"]
    latest = one_handle(library, item, 3)
    previous = one_handle(library, item, 1)
    assert check_citation(caller, CitationCheck(handle=latest)).status == "current"
    assert check_citation(caller, CitationCheck(handle=previous)).status == "newer_version"
    wrong = msgspec.structs.replace(latest, quote_sha256="0" * 64)
    assert check_citation(caller, CitationCheck(handle=wrong)).status == "changed"
    source = provider.meta / "versions" / item / "3"
    source.write_bytes(markdown(["tampered"]))
    assert check_citation(caller, CitationCheck(handle=latest)).status == "changed"
    provider.delete_item(item)
    # The library has not been indexed since the deletion.
    result = check_citation(caller, CitationCheck(handle=previous))
    assert result.status == "withdrawn" and result.latest_version == 3


def test_unindexed_new_version(library):
    provider, item = versions(library)
    handle = one_handle(library, item, 3)
    provider.write_content("reference/versions.md", markdown(["unindexed"]))
    result = check_citation(library["callers"]["admin"], CitationCheck(handle=handle))
    assert result.status == "newer_version" and result.latest_version == 4


def test_row_citation(library):
    provider = SimulatedProvider(library["root"])
    path = "reference/rows.csv"
    provider.write_content(path, b"Parameter,Value\nmass,120\npower,250\n")
    item = next(item for item, row in provider.items().items() if row["path"] == path)
    index_provider(library["settings"])
    caller = library["callers"]["admin"]
    page = read_chunks(caller, ChunkRange(item=item, version=1, from_block=1, to_block=1))
    block = page.chunks[0]
    row = block.text.splitlines()[2]
    handle = msgspec.structs.replace(
        block.handle,
        locator=block.handle.locator + ";row=2",
        quote_sha256=hashlib.sha256(row.encode()).hexdigest(),
    )
    assert check_citation(caller, CitationCheck(handle=handle)).status == "current"
    source = provider.meta / "versions" / item / "1"
    source.write_bytes(source.read_bytes().replace(b"250", b"251"))
    assert check_citation(caller, CitationCheck(handle=handle)).status == "current"
    source.write_bytes(source.read_bytes().replace(b"120", b"121"))
    assert check_citation(caller, CitationCheck(handle=handle)).status == "changed"
    malformed = msgspec.structs.replace(handle, locator=block.handle.locator + ";row=1")
    assert check_citation(caller, CitationCheck(handle=malformed)).status == "changed"


@pytest.mark.slow
def test_ground_truth_versions(demo):
    import os
    import subprocess

    result = subprocess.run(
        [*scripted_model.COMMAND, "index", "--rebuild"],
        env=os.environ
        | scripted_model.ENVIRONMENT
        | {
            "CRAFT_PROVIDER_ROOT": str(demo["one"]),
            "CRAFT_DATABASE_PORT": str(demo["settings"].database_port),
            "CRAFT_DATABASE_NAME": "craft_test",
            "CRAFT_LOG_LEVEL": "WARNING",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    print(result.stdout, end="")
    from app.infrastructure.mcp.stdio import local_caller

    caller = local_caller("admin", demo["settings"])
    assert len(demo["truth"]["versions"]) == 12
    withdrawn = {row["item"] for row in demo["truth"]["deletes"]}
    for row in demo["truth"]["versions"]:
        request = CompareVersions(
            item=row["item"], from_version=row["fromVersion"], to_version=row["toVersion"]
        )
        if row["item"] in withdrawn:
            with pytest.raises(NotFound):
                compare_versions(caller, request)
            with demo["sessions"].begin() as session:
                item = storage.item(session, caller, row["item"])
                assert item is not None and item["deleted_at"] is not None
                retained = {v["version"]: v for v in storage.versions(session, row["item"])}
                assert row["fromVersion"] in retained and row["toVersion"] in retained
                assert storage.differences(
                    session,
                    retained[row["fromVersion"]]["id"],
                    retained[row["toVersion"]]["id"],
                )
            continue
        result = compare_versions(caller, request)
        assert result.blocks and not result.truncated
    with demo["sessions"].begin() as session:
        assert session.execute(select(m.version_diffs.c.seq).limit(1)).first() is not None


def test_access(library, monkeypatch):
    provider = SimulatedProvider(library["root"])
    item = next(
        item for item, row in provider.items().items() if row["mission_key"] == "msn-sim-01"
    )
    handle = one_handle(library, item)
    reads = []
    original = SimulatedProvider.read_content

    def inspected(self, item, version):
        reads.append(item)
        return original(self, item, version)

    monkeypatch.setattr(SimulatedProvider, "read_content", inspected)
    caller = library["callers"]["restricted"]
    with pytest.raises(NotFound):
        check_citation(caller, CitationCheck(handle=handle))
    with pytest.raises(NotFound):
        compare_versions(caller, CompareVersions(item=item, from_version=1, to_version=1))
    assert not reads


def test_live_provider_move_permissions(library, monkeypatch):
    provider = SimulatedProvider(library["root"])
    path = "reference/moved.md"
    provider.write_content(path, markdown(["one"]))
    item = next(item for item, row in provider.items().items() if row["path"] == path)
    index_provider(library["settings"])
    handle = one_handle(library, item)
    target = provider.path("missions/msn-sim-01/engineering/st3-design/moved.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    provider.path(path).rename(target)
    provider.scan()
    with pytest.raises(NotFound):
        check_citation(library["callers"]["restricted"], CitationCheck(handle=handle))


@pytest.mark.slow
def test_http_and_contract(library):
    _, item = versions(library)
    handle = one_handle(library, item)
    app = create_app(library["settings"])
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/library/citations/check", json={"handle": msgspec.to_builtins(handle)}
            ).status_code
            == 401
        )
    from app.library.handlers import CitationCheck, CitationStatus, CompareVersions, VersionDiff
    from app.tests.test_contract import root_schema, typescript_schema

    for shape in [CitationCheck, CitationStatus, CompareVersions, VersionDiff]:
        declared = root_schema(typescript_schema(shape.__name__))
        python = root_schema(msgspec.json.schema(shape))
        assert set(declared["properties"]) == set(python["properties"])
        assert set(declared["required"]) == set(python["required"])
    # Trusted direct calls exercise the same registered operations.
    assert compare_versions(
        replace(library["callers"]["admin"], origin="python"),
        CompareVersions(item=item, from_version=1, to_version=2),
    ).blocks


def test_csv_rename_preserves_citation_on_rebuild(library):
    provider = SimulatedProvider(library["root"])
    provider.write_content("reference/original.csv", b"Parameter,Value\nmass,120\n")
    item = next(
        item for item, row in provider.items().items() if row["path"] == "reference/original.csv"
    )
    index_provider(library["settings"])
    handle = one_handle(library, item)
    provider.path("reference/original.csv").rename(provider.path("reference/renamed.txt"))
    provider.scan()
    index_provider(library["settings"], rebuild=True)
    result = check_citation(library["callers"]["admin"], CitationCheck(handle=handle))
    assert result.status == "current"


def test_provider_read_outside_transactions(library, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    provider, item = versions(library)
    handle = one_handle(library, item, 3)
    active = set()

    def begin(session, transaction):
        if transaction.parent is None:
            active.add(session)

    def end(session, transaction):
        if transaction.parent is None:
            active.discard(session)

    original = SimulatedProvider.read_content
    states = []

    def inspect(self, item, version):
        states.append(not any(session.in_transaction() for session in active))
        return original(self, item, version)

    event.listen(Session, "after_transaction_create", begin)
    event.listen(Session, "after_transaction_end", end)
    monkeypatch.setattr(SimulatedProvider, "read_content", inspect)
    try:
        assert (
            check_citation(library["callers"]["admin"], CitationCheck(handle=handle)).status
            == "current"
        )
        assert states == [True]
    finally:
        event.remove(Session, "after_transaction_create", begin)
        event.remove(Session, "after_transaction_end", end)


def test_real_http_versions(library):
    import secrets

    from app.accounts.handlers import ResetPassword, reset

    _, item = versions(library)
    handle = one_handle(library, item)
    password = secrets.token_urlsafe(24)
    reset(library["callers"]["admin"], ResetPassword(username="demo", password=password))
    with TestClient(create_app(library["settings"]), base_url="https://testserver") as client:
        signed = client.post(
            "/api/session",
            headers={"Origin": "http://localhost:8000"},
            json={"username": "demo", "password": password},
        )
        signed_ok = signed.status_code == 200
        assert signed_ok
        body = {"handle": msgspec.to_builtins(handle)}
        checked = client.post(
            "/api/library/citations/check", headers={"Origin": "http://localhost:8000"}, json=body
        )
        assert checked.status_code == 200 and checked.json()["status"] == "newer_version"
        assert (
            client.post(
                "/api/library/citations/check",
                headers={"Origin": "https://foreign.invalid"},
                json=body,
            ).status_code
            == 403
        )
        compared = client.get(
            f"/api/library/documents/{item}/compare", params={"fromVersion": 1, "toVersion": 3}
        )
        assert compared.status_code == 200 and len(compared.json()["blocks"]) == 8
        assert (
            client.get(
                f"/api/library/documents/{item}/compare", params={"fromVersion": 0, "toVersion": 3}
            ).status_code
            == 400
        )
        assert (
            client.get(
                f"/api/library/documents/{item}/compare",
                params={"fromVersion": 1, "toVersion": 3, "unexpected": "field"},
            ).status_code
            == 400
        )


def test_examples_validate():
    import json
    from pathlib import Path

    import jsonschema

    from app.tests.test_contract import typescript_schema

    for filename, shape in [("citation-status", "CitationStatus"), ("version-diff", "VersionDiff")]:
        jsonschema.validate(
            json.loads(Path(f"app/contract/examples/{filename}.json").read_text()),
            typescript_schema(shape),
        )
