"""The steps of a notebook-run Job: a run folder in, the notebook run within its limits, and
results beside the notebook out."""

import contextlib
import json
import os
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import craft_run
import pytest


@pytest.fixture
def kernel(monkeypatch):
    """nbformat and nbclient live only in the workspace image; these stand in for the names the
    run step uses, on a fake clock. A cell's source is how many seconds it takes, "raise" or
    "die"; anything else breaks the client itself. As in nbclient 0.11.0, a timeout and a dead
    kernel aren't CellExecutionErrors."""

    class CellExecutionError(Exception):
        pass

    class CellTimeoutError(TimeoutError):
        pass

    class DeadKernelError(RuntimeError):
        pass

    state = SimpleNamespace(now=0.0, limits=[])

    class NotebookClient:
        timeout = 0  # the run step sets it before each cell

        def __init__(self, notebook, **options):
            pass

        @contextlib.contextmanager
        def setup_kernel(self):
            yield

        def execute_cell(self, cell, index):
            state.limits.append(self.timeout)
            if cell["source"] == "raise":
                raise CellExecutionError()
            if cell["source"] == "die":
                raise DeadKernelError()
            seconds = float(cell["source"])
            state.now += min(seconds, self.timeout)
            if seconds > self.timeout:
                raise CellTimeoutError()
            cell["outputs"] = ["ran"]

    def read(path, as_version):
        return SimpleNamespace(**json.loads(Path(path).read_text()))

    def write(notebook, path):
        Path(path).write_text(json.dumps(vars(notebook)))

    for name, names in {
        "nbformat": {"read": read, "write": write},
        "nbclient": {"NotebookClient": NotebookClient},
        "nbclient.exceptions": {
            "CellExecutionError": CellExecutionError,
            "CellTimeoutError": CellTimeoutError,
            "DeadKernelError": DeadKernelError,
        },
    }.items():
        module = ModuleType(name)
        vars(module).update(names)
        monkeypatch.setitem(sys.modules, name, module)
    clock = SimpleNamespace(monotonic=lambda: state.now, time=time.time)
    monkeypatch.setattr(craft_run, "time", clock)
    return state


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


def write_notebook(volume, *sources):
    cells = [{"cell_type": "code", "source": source, "outputs": []} for source in sources]
    (volume / "work" / "fit.ipynb").write_text(json.dumps({"cells": cells}))


def test_a_run_cut_short_still_brings_back_its_executed_copy(volume, kernel):
    here = volume / ".craft-runs" / "r1"
    # A cell over its 2 minutes, a dead kernel, a cell that raised and a broken client.
    for last, status, code in (("200", 4, 4), ("die", 5, 5), ("raise", 3, 0), ("?", 2, 2)):
        write_notebook(volume, "1", last, "1")
        craft_run.prepare(volume)
        craft_run.run(volume / "work", here)
        assert (here / ".status").read_text() == str(status)
        assert craft_run.collect(volume) == code
        executed = volume / "work" / "fit.run-1.ipynb"
        cells = json.loads(executed.read_text())["cells"]
        assert [cell["outputs"] for cell in cells] == [["ran"], [], []]  # as far as it got
        executed.unlink()


def test_each_cell_gets_at_most_what_is_left_and_the_run_stops_at_its_total(
    volume, kernel, monkeypatch
):
    monkeypatch.setattr(craft_run, "TOTAL_SECONDS", 150)
    write_notebook(volume, "100", "50", "1")
    craft_run.prepare(volume)
    here = volume / ".craft-runs" / "r1"
    craft_run.run(volume / "work", here)
    assert kernel.limits == [120, 50]  # the third cell never started
    assert (here / ".status").read_text() == str(craft_run.TIMED_OUT)
    assert craft_run.collect(volume) == craft_run.TIMED_OUT
    cells = json.loads((volume / "work" / "fit.run-1.ipynb").read_text())["cells"]
    assert [cell["outputs"] for cell in cells] == [["ran"], ["ran"], []]
