"""Publication checks refuse hidden files, symlinks and arbitrary source/data files."""

from pathlib import Path

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


def test_markdown_is_allowed_only_for_a_published_topic(tmp_path):
    (tmp_path / "markdown/wiki").mkdir(parents=True)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki/runtime.html").write_text("rendered")
    (tmp_path / "markdown/wiki/runtime.md").write_text("source")
    assert public_failures(tmp_path) == []
    for name in ["unexpected.md", "wiki/missing.md", "wiki/runtime.py"]:
        target = tmp_path / "markdown" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("source")
    assert {row["file"] for row in public_failures(tmp_path)} == {
        "markdown/unexpected.md",
        "markdown/wiki/missing.md",
        "markdown/wiki/runtime.py",
    }


def test_markdown_requires_its_rendered_topic(tmp_path):
    (tmp_path / "markdown/wiki").mkdir(parents=True)
    (tmp_path / "markdown/wiki/runtime.md").write_text("source")
    assert public_failures(tmp_path)


def test_prepare_exports_originals_and_links_each_topic(tmp_path):
    from docs.tools.source_links import prepare, source_failures

    originals = tmp_path / "docs"
    staged = tmp_path / "staged"
    output = tmp_path / "output"
    for folder in [originals, staged, output]:
        (folder / "wiki").mkdir(parents=True)
    for name in ["index.md", "wiki/runtime.md"]:
        source = "# " + name + "\n\nPublic documentation.\n"
        (originals / name).write_text(source)
        (staged / name).write_text(source)
    (originals / "tools").mkdir()
    (originals / "tools/private.md").write_text("excluded tool material")
    (originals / ".hidden.md").write_text("excluded hidden material")
    (staged / "index.md").write_text("# Generated index\n")
    assert prepare(originals, staged, output / "markdown") == 2
    for name in ["index.md", "wiki/runtime.md"]:
        assert (output / "markdown" / name).read_bytes() == (originals / name).read_bytes()
        text = (staged / name).read_text()
        assert "Download Markdown" in text and "Edit this page" in text
        assert "github.com/mchdeore/CRAFT/edit/main/docs/" + name in text
        (output / Path(name).with_suffix(".html")).write_text(text)
    assert not (output / "markdown/tools").exists()
    assert source_failures(output, originals) == []
    (output / "markdown/wiki/runtime.md").write_text("changed content")
    assert any(
        row["target"] == "Markdown differs from its public source"
        for row in source_failures(output, originals)
    )


def test_source_check_refuses_missing_download_and_wrong_editor(tmp_path):
    from docs.tools.source_links import source_failures

    originals = tmp_path / "docs"
    originals.mkdir()
    (originals / "index.md").write_text("# Home\n")
    (tmp_path / "index.html").write_text('<a href="https://example.org/edit">Edit</a>')
    failures = source_failures(tmp_path, originals)
    assert {row["target"] for row in failures} == {
        "missing Markdown download",
        "missing Markdown download link",
        "missing GitHub edit link",
    }


def test_staging_copies_only_declared_public_sources(tmp_path):
    from docs.tools.source_links import stage_public_sources

    repository = tmp_path / "repository"
    (repository / "docs/tools").mkdir(parents=True)
    for name in ["index.md", "wiki.css", "ignored.pdf", ".private.md", "tools/script.py"]:
        (repository / "docs" / name).write_text("fixture")
    staged = tmp_path / "staged"
    assert (
        stage_public_sources(
            repository, staged, ["docs/index.md", "docs/wiki.css", "docs/tools/script.py"]
        )
        == 2
    )
    assert {p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file()} == {
        "index.md",
        "wiki.css",
    }


@pytest.mark.parametrize("name", ["docs/../outside.md", "other.md"])
def test_staging_refuses_paths_outside_docs(tmp_path, name):
    from docs.tools.source_links import stage_public_sources

    with pytest.raises(ValueError):
        stage_public_sources(tmp_path, tmp_path / "staged", [name])


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_staging_refuses_symlinked_sources(tmp_path, kind):
    from docs.tools.source_links import stage_public_sources

    repository = tmp_path / "repository"
    (repository / "docs").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.md").write_text("fixture")
    if kind == "file":
        (repository / "docs/index.md").symlink_to(outside / "index.md")
        name = "docs/index.md"
    else:
        (repository / "docs/topics").symlink_to(outside, target_is_directory=True)
        name = "docs/topics/index.md"
    with pytest.raises(ValueError):
        stage_public_sources(repository, tmp_path / "staged", [name])
