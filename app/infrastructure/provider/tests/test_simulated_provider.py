from pathlib import Path

from app.infrastructure.provider.simulated import SimulatedProvider


def test_scan_assigns_items_and_versions(tmp_path: Path) -> None:
    provider = SimulatedProvider(tmp_path)
    for name in ["a", "b", "c"]:
        (tmp_path / f"{name}.md").write_text(name)
    assert len(provider.scan()) == 3
    records = provider.items()
    assert all(len(r["item"]) == 16 and r["versions"][0]["version"] == 1 for r in records.values())
    item = next(r for r in records.values() if r["path"] == "a.md")["item"]
    (tmp_path / "a.md").write_text("changed")
    provider.scan()
    assert provider.get_item(item)["versions"][-1]["version"] == 2
    assert provider.read_content(item, 1) == b"a"


def test_move_and_rename_keep_item(tmp_path: Path) -> None:
    provider = SimulatedProvider(tmp_path)
    (tmp_path / "a.md").write_text("Synthetic")
    provider.scan()
    item = next(iter(provider.items()))
    (tmp_path / "nested").mkdir()
    (tmp_path / "a.md").rename(tmp_path / "nested" / "renamed.md")
    provider.scan()
    record = provider.get_item(item)
    assert record["path"] == "nested/renamed.md" and record["title"] == "renamed"
    assert len(record["versions"]) == 1


def test_delete_keeps_versions(tmp_path: Path) -> None:
    provider = SimulatedProvider(tmp_path)
    provider.write_content("a.md", b"Synthetic")
    item = next(iter(provider.items()))
    provider.delete_item(item)
    assert provider.get_item(item)["deleted_at"] is not None
    assert provider.read_content(item, 1) == b"Synthetic"
    provider.write_content("a.md", b"New synthetic file")
    provider.delete_item(item)
    assert (tmp_path / "a.md").read_bytes() == b"New synthetic file"


def test_change_feed_cursor(tmp_path: Path) -> None:
    provider = SimulatedProvider(tmp_path)
    for _ in range(600):
        provider.feed("itm_000000000001", "changed")
    first, cursor = provider.list_changes(0)
    second, last = provider.list_changes(cursor)
    assert len(first) == 500 and len(second) == 100 and cursor == 500 and last == 600


def test_path_tags(tmp_path: Path) -> None:
    provider = SimulatedProvider(tmp_path)
    tags = provider.tags("missions/msn-sim-01/engineering/st3-design/x.md")
    assert (
        tags["mission_key"] == "msn-sim-01"
        and tags["silo"] == "engineering"
        and tags["stage"] == "st3-design"
    )
    unknown = provider.tags("missions/msn-sim-01/unknown/st3-design/x.md")
    assert (
        unknown["silo"] is None
        and unknown["stage"] is None
        and unknown["warnings"] == ["unknown_silo"]
    )


def test_version_preserves_original_name_and_media(tmp_path):
    provider = SimulatedProvider(tmp_path)
    provider.write_content("reference/rows.csv", b"Simulated,value\nOne,1\n")
    item = next(iter(provider.items()))
    provider.path("reference/rows.csv").rename(provider.path("reference/renamed.txt"))
    provider.scan()
    version = provider.get_item(item)["versions"][0]
    assert version["file_name"] == "rows.csv" and version["media_type"] == "text/csv"
    assert len(provider.get_item(item)["versions"]) == 1
