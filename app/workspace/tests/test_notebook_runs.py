"""Whole-notebook runs queue a job, run in a sandbox and copy back without overwriting."""

import asyncio
import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest

from app.approvals.tests.test_decisions import context as context
from app.infrastructure import notebook_sandbox, workspaces
from app.workspace import service
from app.workspace.handlers import RunNotebook, run_notebook
from app.workspace.tests.test_actions import workspace as workspace

NOTEBOOK = {
    "cells": [{"cell_type": "code", "metadata": {}, "source": "1", "outputs": []}],
    "metadata": {},
    "nbformat": 4,
    "nbformat_minor": 5,
}


def test_a_person_can_queue_more_than_two_notebook_runs(context, workspace, monkeypatch):
    _, files = workspace
    files["work/fit.ipynb"] = NOTEBOOK
    own = replace(context["caller"], origin="http")
    queued = [asyncio.run(run_notebook(own, RunNotebook(path="work/fit.ipynb"))) for _ in range(3)]
    assert len({run.job_id for run in queued}) == 3
    assert all(run.notebook == "work/fit.ipynb" for run in queued)
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


def _cluster(answers, requests):
    """A fake Kubernetes API: each GET of the Job pops its next status."""
    import httpx

    from app.infrastructure.notebook_jobs import Cluster

    def handle(request):
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(201, json={})
        if request.method == "GET":
            return httpx.Response(200, json={"status": answers.pop(0)})
        return answers.pop(0) if answers else httpx.Response(200, json={})

    return Cluster("https://k8s", "craft", {}, transport=httpx.MockTransport(handle))


def test_a_run_job_is_offline_read_only_and_bounded():
    from app.infrastructure.notebook_jobs import manifest

    user = uuid4()
    job = manifest("notebook-run-1", "craft-user-workspace:local", user, "work", "fit.ipynb", "o")
    pod = job["spec"]["template"]["spec"]
    assert job["spec"]["template"]["metadata"]["labels"] == {"craft-notebook-run": "true"}
    assert pod["automountServiceAccountToken"] is False and job["spec"]["backoffLimit"] == 0
    assert job["spec"]["activeDeadlineSeconds"] <= 20 * 60
    assert pod["volumes"][0]["persistentVolumeClaim"]["claimName"] == f"claim-{user}"
    prepare, run = pod["initContainers"]
    (collect,) = pod["containers"]
    mounts = {mount["mountPath"]: mount for mount in run["volumeMounts"]}
    assert mounts["/work"] == {
        "name": "volume", "mountPath": "/work", "subPath": "work", "readOnly": True
    }  # fmt: skip
    assert mounts["/work/outputs"]["subPath"] == ".craft-runs/notebook-run-1/outputs"
    assert "/workspace" not in mounts  # only the trusted steps see the whole volume
    assert run["resources"]["limits"] == {"memory": "2Gi", "cpu": "2"}
    for container in (prepare, run, collect):
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert container["securityContext"]["runAsNonRoot"] is True


def test_a_finished_failed_or_timed_out_job_is_reported_and_deleted(monkeypatch):
    import httpx

    from app.infrastructure import notebook_jobs
    from app.infrastructure.notebook_sandbox import SandboxError

    monkeypatch.setattr(notebook_jobs, "POLL_SECONDS", 0)
    done = {"conditions": [{"type": "Complete", "status": "True"}]}
    failed = {
        "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}]
    }
    late = {"conditions": [{"type": "Failed", "status": "True", "reason": "DeadlineExceeded"}]}
    for answers, error in (([{}, done], None), ([failed], "run_failed"), ([late], "run_timeout")):
        requests = []
        cluster = _cluster([*answers, httpx.Response(404)], requests)
        call = notebook_jobs.run(cluster, "image", uuid4(), "", "nb.ipynb", "o.ipynb")
        if error is None:
            asyncio.run(call)
        else:
            with pytest.raises(SandboxError, match=error):
                asyncio.run(call)
        assert requests[0] == ("POST", "/apis/batch/v1/namespaces/craft/jobs")
        assert requests[-1][0] == "DELETE"  # deleting a Job that is already gone is quiet


def test_a_cancelled_run_deletes_its_job_and_a_failed_cleanup_is_logged(monkeypatch, caplog):
    import httpx

    from app.infrastructure import notebook_jobs

    monkeypatch.setattr(notebook_jobs, "POLL_SECONDS", 0.01)
    requests = []
    running: list[Any] = [{"active": 1}] * 1000
    cluster = _cluster(running, requests)

    async def cancel():
        task = asyncio.ensure_future(
            notebook_jobs.run(cluster, "image", uuid4(), "", "nb.ipynb", "o.ipynb")
        )
        await asyncio.sleep(0.05)
        running[:] = [httpx.Response(500)]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    assert requests[-1][0] == "DELETE"
    (line,) = [r for r in caplog.records if r.getMessage() == "notebook run cleanup failed"]
    fields = line.__dict__["fields"]
    assert fields["name"].startswith("notebook-run-") and fields["error_type"] == "HTTPStatusError"
