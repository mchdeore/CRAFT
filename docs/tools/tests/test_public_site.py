"""Publication checks refuse hidden files, symlinks and arbitrary source/data files."""

import pytest

from docs.tools.check_site import check, public_failures


def test_rendered_preview_and_known_generated_assets_are_allowed(tmp_path):
    (tmp_path / "preview").mkdir()
    (tmp_path / "search").mkdir()
    (tmp_path / "index.html").write_text('<a href="preview/">Preview</a>')
    (tmp_path / "preview/index.html").write_text('<a href="../index.html">Documents</a>')
    for name in [
        "CRAFT-Spec.pdf",
        "theme.css",
        "theme.js",
        "font.woff2",
        "diagram.mmd",
        "sitemap.xml.gz",
        "search/search_index.json",
    ]:
        (tmp_path / name).write_bytes(b"")
    assert public_failures(tmp_path) == []
    assert check(tmp_path)["failures"] == []


@pytest.mark.parametrize(
    "name", ["source.py", "script.sh", "data.csv", "book.ipynb", "key.json", "snapshot.parquet"]
)
def test_unrecognized_source_or_data_is_refused(tmp_path, name):
    (tmp_path / name).write_text("generated")
    assert public_failures(tmp_path) == [{"file": name, "target": "unsupported public artifact"}]


def test_hidden_artifact_and_symlink_are_refused(tmp_path):
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden/index.html").write_text("generated")
    (tmp_path / "index.html").write_text("generated")
    (tmp_path / "alias.html").symlink_to(tmp_path / "index.html")
    refused = {row["file"] for row in public_failures(tmp_path)}
    assert refused == {".hidden", ".hidden/index.html", "alias.html"}


def test_escaping_link_is_refused(tmp_path):
    (tmp_path / "index.html").write_text('<a href="../../outside.html">Outside</a>')
    assert check(tmp_path)["failures"] == [{"file": "index.html", "target": "../../outside.html"}]
