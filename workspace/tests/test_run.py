"""The trusted steps of a notebook-run Job: a run folder in, results beside the notebook out."""

import os

import craft_run
import pytest


@pytest.fixture
def volume(tmp_path, monkeypatch):
    for key, value in {
        "RUN": "r1",
        "FOLDER": "work",
        "NOTEBOOK": "fit.ipynb",
        "OUTPUT": "fit.run-1.ipynb",
    }.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "fit.ipynb").write_text("{}")
    return tmp_path


def finish_run(volume, status="0", outputs=None):
    """What the run step leaves: the executed copy, its status and its outputs."""
    here = volume / ".craft-runs" / "r1"
    (here / "fit.run-1.ipynb").write_text('{"executed": true}')
    (here / ".status").write_text(status)
    for name, content in (outputs or {}).items():
        (here / "outputs" / name).parent.mkdir(parents=True, exist_ok=True)
        (here / "outputs" / name).write_text(content)
    return here


def test_results_land_beside_the_notebook_and_never_overwrite(volume):
    outputs = volume / "work" / "outputs"
    outputs.mkdir()
    (outputs / "table.csv").write_text("old\n")
    (outputs / "same.csv").write_text("x")
    (volume / "work" / "fit.run-1.ipynb").write_text("someone else's")
    craft_run.prepare(volume)
    here = volume / ".craft-runs" / "r1"
    assert (here / "outputs" / "same.csv").read_text() == "x"  # the run sees its old outputs
    finish_run(volume, outputs={"table.csv": "new\n", "same.csv": "x", "deep/chart.png": "png"})
    os.symlink("/etc/hostname", here / "outputs" / "link")

    assert craft_run.collect(volume) == 0
    assert (outputs / "table.csv").read_text() == "old\n"
    (renamed,) = outputs.glob("table.run-*.csv")
    assert renamed.read_text() == "new\n"
    assert not list(outputs.glob("same.run-*")) and (outputs / "deep" / "chart.png").exists()
    assert not (outputs / "link").exists()
    assert (volume / "work" / "fit.run-1.ipynb").read_text() == "someone else's"
    (executed,) = (volume / "work").glob("fit.run-1.run-*.ipynb")
    assert "executed" in executed.read_text()
    assert not here.exists() and (volume / "work" / "fit.ipynb").read_text() == "{}"


def test_an_outputs_folder_made_for_the_run_goes_if_it_stays_empty(volume):
    craft_run.prepare(volume)
    assert (volume / "work" / "outputs").is_dir()  # the run's outputs/ is mounted over it
    finish_run(volume)
    assert craft_run.collect(volume) == 0
    assert not (volume / "work" / "outputs").exists()
    assert (volume / "work" / "fit.run-1.ipynb").exists()


def test_a_run_without_its_executed_copy_or_with_a_failure_fails(volume):
    craft_run.prepare(volume)
    assert craft_run.collect(volume) == 1  # nothing to move: never reported as succeeded
    finish_run(volume, status="3")
    assert craft_run.collect(volume) == 0  # a failing cell still returns the run
    craft_run.prepare(volume)
    finish_run(volume, status="1")
    assert craft_run.collect(volume) == 2
