"""Both sources read the same originals; the setting alone picks one, and neither reindexes."""

from dataclasses import dataclass

import httpx
import pytest
from starlette.testclient import TestClient

from app.infrastructure.provider import source_provider
from app.infrastructure.provider.livelink import LiveLinkProvider, item_key, node_id
from app.infrastructure.provider.livelink_api import livelink_api
from app.infrastructure.provider.simulated import SimulatedProvider

PATH = "missions/msn-sim-01/engineering/st3-design/thermal.txt"
TICKET = "generated-ticket"


@pytest.fixture
def folder(tmp_path):
    """A provider folder with one document in two versions, as the frozen set has."""
    root = tmp_path / "provider"
    file = root / PATH
    file.parent.mkdir(parents=True)
    writer = SimulatedProvider(root, clock="2026-01-01T00:00:00+00:00")
    file.write_bytes(b"first")
    writer.scan()
    file.write_bytes(b"x" * 100)
    writer.scan()
    return root


def livelink(root, denied=None, ticket=TICKET):
    api = TestClient(livelink_api(root, TICKET, denied), base_url="http://livelink.test")

    def handle(request: httpx.Request) -> httpx.Response:
        # The in-process API over HTTP semantics; the adapter reads the answer as usual.
        answer = api.request(request.method, str(request.url), headers=dict(request.headers))
        return httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)

    return LiveLinkProvider(
        root, "http://livelink.test", ticket, transport=httpx.MockTransport(handle)
    )


@pytest.fixture(params=["simulated", "livelink"])
def source(request, folder):
    return SimulatedProvider(folder) if request.param == "simulated" else livelink(folder)


def the_item(source):
    (record,) = source.items().values()
    return record


def test_both_read_the_same_records_and_original_bytes(source, folder):
    record = the_item(source)
    assert record["path"] == PATH and record["mission_key"] == "msn-sim-01"
    assert [v["version"] for v in record["versions"]] == [1, 2]
    assert source.get_item(record["item"]) == record
    assert source.read_content(record["item"], 1) == b"first"
    assert source.read_content(record["item"], 2) == b"x" * 100
    assert source.list_changes(0) == SimulatedProvider(folder).list_changes(0)


def test_unknown_versions(source):
    item = the_item(source)["item"]
    with pytest.raises(KeyError):
        source.read_content(item, 9)
    with pytest.raises(KeyError):
        source.get_item("itm_000000000000")


def test_a_withdrawn_version_is_unreadable(source, folder):
    item = the_item(source)["item"]
    (folder / ".provider" / "versions" / item / "1").unlink()
    with pytest.raises((KeyError, FileNotFoundError)):
        source.read_content(item, 1)
    assert source.read_content(item, 2) == b"x" * 100


def test_the_api_refuses_a_wrong_ticket_and_a_denied_node(folder):
    item = the_item(SimulatedProvider(folder))["item"]
    with pytest.raises(httpx.HTTPStatusError):  # an error, not a missing document
        livelink(folder, ticket="wrong").read_content(item, 1)
    with pytest.raises(KeyError):
        livelink(folder, denied={node_id(item)}).read_content(item, 1)
    assert item_key(node_id(item)) == item


def test_the_livelink_adapter_never_scans(folder):
    with pytest.raises(RuntimeError, match="frozen"):
        livelink(folder).scan()


def test_a_permission_denied_folder_is_not_a_deletion(folder):
    provider = SimulatedProvider(folder)
    locked = folder / "missions" / "msn-sim-01"
    locked.chmod(0)
    try:
        assert provider.scan() == []
        assert the_item(provider)["deleted_at"] is None
    finally:
        locked.chmod(0o755)
    (folder / PATH).unlink()
    assert [change["kind"] for change in provider.scan()] == ["deleted"]


def test_the_setting_alone_picks_the_adapter(folder):
    @dataclass
    class Settings:
        provider: str
        provider_root: str = str(folder)
        provider_url: str = "http://livelink.test"
        provider_token: str = TICKET

    assert isinstance(source_provider(Settings("simulated")), SimulatedProvider)
    assert isinstance(source_provider(Settings("livelink_test")), LiveLinkProvider)
