"""Whole-notebook runs queue a job, run in a sandbox and copy back without overwriting."""

import asyncio
import json
from dataclasses import replace

import pytest

from app.approvals.tests.test_decisions import context as context
from app.infrastructure import notebook_sandbox, workspaces
from app.infrastructure.errors import Conflict
from app.workspace import service
from app.workspace.handlers import RunNotebook, run_notebook
from app.workspace.tests.test_actions import workspace as workspace

NOTEBOOK = {
    "cells": [{"cell_type": "code", "metadata": {}, "source": "1", "outputs": []}],
    "metadata": {},
    "nbformat": 4,
    "nbformat_minor": 5,
}


def test_at_most_two_runs_queue_per_requester(context, workspace, monkeypatch):
    _, files = workspace
    files["work/fit.ipynb"] = NOTEBOOK
    own = replace(context["caller"], origin="http")
    first = asyncio.run(run_notebook(own, RunNotebook(path="work/fit.ipynb")))
    assert first.notebook == "work/fit.ipynb" and first.output.startswith("work/fit.run-")
    asyncio.run(run_notebook(own, RunNotebook(path="work/fit.ipynb")))
    with pytest.raises(Conflict):
        asyncio.run(run_notebook(own, RunNotebook(path="work/fit.ipynb")))
    from sqlalchemy import text

    with context["owner"].begin() as connection:
        connection.execute(
            text("delete from procrastinate_jobs where args->>'requested_by' = :user"),
            {"user": str(context["caller"].user_id)},
        )


def test_a_run_copies_back_its_notebook_and_outputs_without_overwriting(
    context, workspace, monkeypatch
):
    _, files = workspace
    written = {}
    existing = {"work/outputs/table.csv": b"old\n", "work/fit.ipynb": b"{}"}

    async def read_folder(target, folder, limit):
        assert folder == "work"
        return {"fit.ipynb": b"{}", "outputs/table.csv": b"old\n", "outputs/same.csv": b"x"}

    async def exists(target, path):
        return path in existing or path in written

    async def write_bytes(target, path, content):
        written[path] = content

    class FakeSandbox:
        def __init__(self, image):
            self.image = image

        def run(self, files, notebook, output):
            assert notebook == "fit.ipynb" and "outputs/table.csv" in files
            return 0, {
                output: json.dumps(NOTEBOOK).encode(),
                "outputs/table.csv": b"new\n",
                "outputs/same.csv": b"x",
                "outputs/chart.png": b"png",
            }

        def kill(self):
            pass

    monkeypatch.setattr(workspaces, "read_folder", read_folder)
    monkeypatch.setattr(workspaces, "exists", exists)
    monkeypatch.setattr(workspaces, "write_bytes", write_bytes)
    monkeypatch.setattr(notebook_sandbox, "Sandbox", FakeSandbox)
    own = replace(context["caller"], origin="http")
    asyncio.run(service.notebook_job(own, "work/fit.ipynb", "fit.run-1.ipynb"))
    assert written["work/fit.run-1.ipynb"] == json.dumps(NOTEBOOK).encode()
    assert written["work/outputs/chart.png"] == b"png"
    renamed = [name for name in written if name.startswith("work/outputs/table.run-")]
    assert len(renamed) == 1 and written[renamed[0]] == b"new\n"
    assert "work/outputs/table.csv" not in written and "work/outputs/same.csv" not in written
    assert "work/fit.ipynb" not in written


def _notebook(*sources):
    return json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "metadata": {},
                    "source": s,
                    "outputs": [],
                    "execution_count": None,
                }
                for s in sources
            ],
            "metadata": {
                "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    ).encode()


@pytest.mark.docker
def test_sandbox_runs_offline_and_stops_at_once_when_cancelled():
    import threading
    import time

    image = "craft-user-workspace:local-pilot"
    code = (
        "import socket, pathlib\n"
        "pathlib.Path('outputs').mkdir(exist_ok=True)\n"
        "pathlib.Path('outputs/answer.txt').write_text('42')\n"
        "try:\n    socket.create_connection(('1.1.1.1', 80), timeout=3); print('online')\n"
        "except OSError:\n    print('offline')\n"
    )
    status, made = notebook_sandbox.Sandbox(image).run(
        {"nb.ipynb": _notebook(code)}, "nb.ipynb", "nb.run.ipynb"
    )
    assert status == 0 and made["outputs/answer.txt"] == b"42"
    executed = json.loads(made["nb.run.ipynb"])
    text = "".join("".join(o.get("text", "")) for o in executed["cells"][0]["outputs"])
    assert "offline" in text and "online" not in text.replace("offline", "")

    sandbox = notebook_sandbox.Sandbox(image)
    result = {}

    def run():
        try:
            sandbox.run(
                {"slow.ipynb": _notebook("import time; time.sleep(600)")}, "slow.ipynb", "o.ipynb"
            )
        except Exception as error:
            result["error"] = error

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(8)
    started = time.monotonic()
    sandbox.kill()
    worker.join(10)
    assert not worker.is_alive() and time.monotonic() - started < 5
    assert "error" in result
