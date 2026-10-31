"""Starter skills follow the image on existing volumes and never overwrite the person's edits."""

import hashlib
import json
import os
import runpy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

CONFIG = Path(__file__).parents[1] / "jupyter_server_config.py"
MANIFEST = ".craft-starter.json"


def start(volume, settings):
    """Run the shipped config as a server start on this volume; returns its globals."""
    environment = {"USER_ROOT": str(volume.resolve()), "JUPYTERHUB_API_TOKEN": "generated-token"}
    with patch.dict(os.environ, environment, clear=True):
        return runpy.run_path(str(CONFIG), init_globals={"get_config": lambda: settings})


@pytest.fixture
def config(tmp_path):
    """The shipped config's globals, from one start on a fresh volume."""
    (tmp_path / "volume").mkdir()
    return start(tmp_path / "volume", MagicMock())


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def image(starter, **skills):
    """The image's starter skills folder, holding exactly these skills."""
    starter.mkdir(exist_ok=True)
    for old in starter.glob("*.md"):
        old.unlink()
    for name, text in skills.items():
        (starter / f"{name}.md").write_text(text)
    return starter


def files(skills):
    """The plain files in a skills folder, without links and folders."""
    plain = (path for path in skills.glob("*.md") if path.is_file() and not path.is_symlink())
    return {path.name: path.read_text() for path in plain}


def test_a_fresh_volume_gets_every_shipped_skill_and_a_manifest(config, tmp_path):
    shipped = {path.name: path.read_bytes() for path in (CONFIG.parent / "skills").glob("*.md")}
    skills = tmp_path / "volume" / "skills"
    assert {path.name: path.read_bytes() for path in skills.glob("*.md")} == shipped
    manifest = json.loads((skills / MANIFEST).read_text())
    assert manifest == {name: sha256(content) for name, content in shipped.items()}


def test_unmodified_copies_follow_the_image_and_edits_links_and_folders_stay(config, tmp_path):
    sync, starter, root = config["sync_starter_skills"], tmp_path / "image", tmp_path / "root"
    skills = root / "skills"
    root.mkdir()
    sync(image(starter, updated="v1", edited="v1", linked="v1", folder="v1"), root)
    (skills / "edited.md").write_text("my notes")
    # The link's target still holds the placed copy, so following it would overwrite it.
    (tmp_path / "elsewhere.md").write_text("v1")
    (skills / "linked.md").unlink()
    (skills / "linked.md").symlink_to(tmp_path / "elsewhere.md")
    (skills / "folder.md").unlink()
    (skills / "folder.md").mkdir()

    for _ in range(2):
        sync(image(starter, updated="v2", edited="v2", linked="v2", folder="v2"), root)
        assert files(skills) == {"updated.md": "v2", "edited.md": "my notes"}
        assert (skills / "linked.md").is_symlink()
        assert (tmp_path / "elsewhere.md").read_text() == "v1"
        assert (skills / "folder.md").is_dir()


def test_a_dropped_skill_goes_unless_edited_and_a_deleted_copy_stays_gone(config, tmp_path):
    sync, starter, root = config["sync_starter_skills"], tmp_path / "image", tmp_path / "root"
    skills = root / "skills"
    root.mkdir()
    sync(image(starter, deleted="v1", dropped="v1", edited="v1"), root)
    (skills / "deleted.md").unlink()
    (skills / "edited.md").write_text("my notes")

    for _ in range(2):
        sync(image(starter, deleted="v2"), root)
        assert files(skills) == {"edited.md": "my notes"}
        assert json.loads((skills / MANIFEST).read_text()) == {"deleted.md": sha256(b"v1")}


@pytest.mark.parametrize("manifest", [None, "{not json"])
def test_a_volume_without_a_manifest_updates_its_unmodified_copies(config, tmp_path, manifest):
    sync, starter, root = config["sync_starter_skills"], tmp_path / "image", tmp_path / "root"
    skills = root / "skills"
    skills.mkdir(parents=True)
    for name, text in {"old": "v1", "current": "v2", "edited": "my notes", "gone": "v1"}.items():
        (skills / f"{name}.md").write_text(text)
    if manifest is not None:
        (skills / MANIFEST).write_text(manifest)
    known = {name: (sha256(b"v1"),) for name in ("old.md", "edited.md", "gone.md")}

    with patch.dict(config["KNOWN_HASHES"], known, clear=True):
        sync(image(starter, old="v2", current="v2", edited="v2"), root)
    assert files(skills) == {"old.md": "v2", "current.md": "v2", "edited.md": "my notes"}
    assert json.loads((skills / MANIFEST).read_text()) == {
        "old.md": sha256(b"v2"),
        "current.md": sha256(b"v2"),
    }


def test_a_failed_sync_leaves_the_rest_of_the_config_in_place(tmp_path, capsys):
    (tmp_path / "skills").write_text("not a folder")
    settings = MagicMock()
    start(tmp_path, settings)
    assert settings.ServerApp.root_dir == str(tmp_path.resolve())
    assert "Starter skills were not synced" in capsys.readouterr().err
