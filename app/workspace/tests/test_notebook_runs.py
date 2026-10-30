"""Whole-notebook runs queue a job, run through a NotebookRunner and never overwrite a file.

The service codes only against ``NotebookRunner``; these tests cover how it picks a backend, the
Docker sandbox's copy-back (``SandboxRunner``) and the Kubernetes Job's lifecycle (``JobRunner``),
including failures, cancellation and what reaches the logs.
"""

import asyncio
import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest

from app.approvals.tests.test_decisions import context as context
from app.infrastructure import notebook_jobs, notebook_sandbox, workspaces
from app.infrastructure.notebook_runs import NotebookRunner, RunError
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


class FakeSandbox:
    """Stands in for the Docker container: returns ``made`` with exit ``code``, or blocks."""

    killed = 0

    def __init__(self, image, code=0, made=None, block=None):
        self.image, self.code, self.made, self.block = image, code, made or {}, block

    def run(self, files, notebook, output):
        if self.block is not None:
            self.block.wait(5)
        return self.code, self.made

    def kill(self):
        FakeSandbox.killed += 1
        if self.block is not None:
            self.block.set()


def _sandbox_runner(monkeypatch, written, **fake):
    async def read_folder(target, folder, limit):
        return {"nb.ipynb": b"{}"}

    async def exists(target, path):
        return False

    async def write_bytes(target, path, content):
        written[path] = content

    monkeypatch.setattr(workspaces, "read_folder", read_folder)
    monkeypatch.setattr(workspaces, "exists", exists)
    monkeypatch.setattr(workspaces, "write_bytes", write_bytes)
    monkeypatch.setattr(notebook_sandbox, "Sandbox", lambda image: FakeSandbox(image, **fake))
    target = workspaces.JupyterTarget("http://jupyter/user/x/", "generated-never-sent", "")
    runner: NotebookRunner = notebook_sandbox.SandboxRunner(target, "image")
    return runner


def test_a_sandbox_run_that_fails_keeps_its_results_and_reports_a_fixed_code(monkeypatch):
    written = {}
    runner = _sandbox_runner(monkeypatch, written, code=1, made={"o.ipynb": b"{}"})
    with pytest.raises(RunError, match="^run_failed$"):
        asyncio.run(runner.run("", "nb.ipynb", "o.ipynb"))
    assert written == {"o.ipynb": b"{}"}  # the executed copy shows where it stopped
    # A cell that raised (exit 3) is a finished run, with the error in the executed copy.
    asyncio.run(_sandbox_runner(monkeypatch, {}, code=3).run("", "nb.ipynb", "o.ipynb"))


def test_a_sandbox_result_craft_cannot_write_is_skipped_and_the_rest_written(monkeypatch, caplog):
    import logging

    caplog.set_level(logging.INFO)
    written = {}
    made = {
        "../../escape.txt": b"x",  # leaves the folder
        "outputs/.cache/state": b"c",  # hidden: CRAFT never writes these
        "outputs/table.csv": b"t",
        "o.ipynb": b"{}",
    }
    runner = _sandbox_runner(monkeypatch, written, made=made)
    asyncio.run(runner.run("work", "nb.ipynb", "o.ipynb"))
    assert written == {"work/outputs/table.csv": b"t", "work/o.ipynb": b"{}"}
    (line,) = [r for r in caplog.records if r.getMessage() == "notebook run skipped results"]
    assert line.__dict__["fields"] == {"skipped": 2}  # a count, never the names


def test_a_folder_over_the_limit_fails_before_anything_runs(monkeypatch):
    written = {}
    runner = _sandbox_runner(monkeypatch, written)

    async def too_large(target, folder, limit):
        raise ValueError("The notebook folder is too large")

    monkeypatch.setattr(workspaces, "read_folder", too_large)
    monkeypatch.setattr(notebook_sandbox, "Sandbox", None)  # never reached
    with pytest.raises(RunError, match="^folder_too_large$"):
        asyncio.run(runner.run("work", "nb.ipynb", "o.ipynb"))
    assert written == {}


def test_the_queued_job_outlasts_every_backend_limit():
    from app.infrastructure.notebook_runs import JOB_SECONDS, TOTAL_SECONDS
    from app.workspace.jobs import notebook_run

    job = notebook_jobs.manifest("notebook-run-1", "image", uuid4(), "", "nb.ipynb", "o")
    # The Job's deadline and the sandbox's run limit fire first, so a slow run is a run_timeout.
    assert TOTAL_SECONDS < job["spec"]["activeDeadlineSeconds"] < JOB_SECONDS
    queued = notebook_run.__dict__["__operation__"].job
    assert queued is not None and queued.timeout == JOB_SECONDS


def test_cancelling_a_sandbox_run_kills_its_container(monkeypatch):
    import threading

    block = threading.Event()
    runner = _sandbox_runner(monkeypatch, {}, block=block)
    FakeSandbox.killed = 0

    async def cancel():
        task = asyncio.ensure_future(runner.run("", "nb.ipynb", "o.ipynb"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    assert FakeSandbox.killed == 1


def test_on_the_cluster_the_service_runs_a_job_for_the_caller(context, workspace, monkeypatch):
    from app.infrastructure import hub

    started = []

    async def run(self, folder, notebook, output):
        started.append((self.owner, self.image, folder, notebook, output))

    async def stopped(found, user_id):
        return "stopped"

    # A Job reads the person's volume itself, so their JupyterLab server may be stopped.
    monkeypatch.setattr(hub.Hub, "state", stopped)
    monkeypatch.setattr(hub.Hub, "kernel", None)  # never reached on the cluster
    cluster = notebook_jobs.Cluster("https://k8s", "craft", {})
    monkeypatch.setattr(notebook_jobs, "in_cluster", lambda: cluster)
    monkeypatch.setattr(notebook_jobs.JobRunner, "run", run)
    monkeypatch.setattr(notebook_sandbox, "Sandbox", None)  # never reached on the cluster
    own = replace(context["caller"], origin="http")
    asyncio.run(service.notebook_job(own, "work/fit.ipynb", "fit.run-1.ipynb"))
    image = own.settings.notebook_image
    assert started == [(own.user_id, image, "work", "fit.ipynb", "fit.run-1.ipynb")]


def test_each_backend_stands_alone():
    import ast
    import inspect

    for module, other in ((notebook_jobs, "notebook_sandbox"), (notebook_sandbox, "notebook_jobs")):
        imported = {
            name
            for node in ast.walk(ast.parse(inspect.getsource(module)))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in [getattr(node, "module", None) or "", *(a.name for a in node.names)]
        }
        assert not any(other in name for name in imported), module.__name__


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

    headers = {"Authorization": "Bearer generated-service-account-token"}
    return Cluster("https://k8s", "craft", headers, transport=httpx.MockTransport(handle))


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
    (rule,) = job["spec"]["podFailurePolicy"]["rules"]  # collect's code for a run cut short
    assert rule["onExitCodes"]["containerName"] == collect["name"]
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

    monkeypatch.setattr(notebook_jobs, "POLL_SECONDS", 0)
    done = {"conditions": [{"type": "Complete", "status": "True"}]}
    failed = {
        "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}]
    }
    late = {"conditions": [{"type": "Failed", "status": "True", "reason": "DeadlineExceeded"}]}

    def cut_short(code):  # the pod failure policy matched collect's exit code
        message = (
            "Container collect for pod craft/notebook-run-1-x7k2p failed with exit code "
            f"{code} matching FailJob rule at index 0"
        )
        reason = {"type": "Failed", "status": "True", "reason": "PodFailurePolicy"}
        return {"conditions": [{**reason, "message": message}]}

    for answers, error in (
        ([{}, done], None),
        ([failed], "run_failed"),
        ([late], "run_timeout"),
        ([cut_short(4)], "run_timeout"),
        ([cut_short(5)], "kernel_died"),
        ([cut_short(9)], "run_failed"),
    ):
        requests = []
        cluster = _cluster([*answers, httpx.Response(404)], requests)
        runner: NotebookRunner = notebook_jobs.JobRunner(cluster, "image", uuid4())
        call = runner.run("", "nb.ipynb", "o.ipynb")
        if error is None:
            asyncio.run(call)
        else:
            with pytest.raises(RunError, match=f"^{error}$"):
                asyncio.run(call)
        assert requests[0] == ("POST", "/apis/batch/v1/namespaces/craft/jobs")
        assert requests[-1][0] == "DELETE"  # deleting a Job that is already gone is quiet


def test_only_a_blip_while_polling_is_asked_again_and_the_job_is_deleted_once(monkeypatch):
    import httpx

    monkeypatch.setattr(notebook_jobs, "POLL_SECONDS", 0)
    done = {"status": {"conditions": [{"type": "Complete", "status": "True"}]}}
    answers: list[Any] = [httpx.Response(503), "reset", httpx.Response(429), done]
    methods = []

    def handle(request):
        methods.append(request.method)
        if request.method != "GET":
            return httpx.Response(201 if request.method == "POST" else 200, json={})
        answer = answers.pop(0)
        if answer == "reset":
            raise httpx.ConnectError("connection reset", request=request)
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    transport = httpx.MockTransport(handle)
    cluster = notebook_jobs.Cluster("https://k8s", "craft", {}, transport=transport)
    asyncio.run(notebook_jobs.JobRunner(cluster, "image", uuid4()).run("", "nb.ipynb", "o.ipynb"))
    assert methods == ["POST", "GET", "GET", "GET", "GET", "DELETE"]

    answers[:], methods[:] = [httpx.Response(503), httpx.Response(404)], []
    with pytest.raises(httpx.HTTPStatusError):  # a missing Job is no blip
        asyncio.run(notebook_jobs.JobRunner(cluster, "image", uuid4()).run("", "nb.ipynb", "o"))
    assert methods == ["POST", "GET", "GET", "DELETE"]


def test_the_runner_in_the_image_keeps_the_same_limits_and_codes():
    import craft_run

    from app.infrastructure.notebook_runs import CELL_SECONDS, TOTAL_SECONDS

    assert (craft_run.TOTAL_SECONDS, craft_run.CELL_SECONDS) == (TOTAL_SECONDS, CELL_SECONDS)
    cut_short = {craft_run.TIMED_OUT: "run_timeout", craft_run.KERNEL_DIED: "kernel_died"}
    assert notebook_jobs.CUT_SHORT == cut_short


def test_a_cancelled_run_deletes_its_job_and_a_failed_cleanup_is_logged(monkeypatch, caplog):
    import httpx

    monkeypatch.setattr(notebook_jobs, "POLL_SECONDS", 0.01)
    requests = []
    running: list[Any] = [{"active": 1}] * 1000
    cluster = _cluster(running, requests)

    async def cancel():
        task = asyncio.ensure_future(
            notebook_jobs.JobRunner(cluster, "image", uuid4()).run("", "nb.ipynb", "o.ipynb")
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


def test_a_refused_or_lost_create_still_deletes_by_name_and_no_log_holds_the_token(
    monkeypatch, caplog
):
    import logging

    import httpx

    requests = []
    refused = httpx.Response(403, json={"message": "jobs is forbidden"})

    def handle(request):
        requests.append((request.method, request.url.path))
        return refused if request.method == "POST" else httpx.Response(500)

    headers = {"Authorization": "Bearer generated-service-account-token"}
    transport = httpx.MockTransport(handle)
    cluster = notebook_jobs.Cluster("https://k8s", "craft", headers, transport=transport)
    assert "generated-service-account-token" not in repr(cluster)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(httpx.HTTPStatusError) as raised:
        asyncio.run(notebook_jobs.JobRunner(cluster, "image", uuid4()).run("", "nb.ipynb", "o"))
    # Deleting by name after any create covers one the API stored before its answer was lost.
    assert [method for method, _ in requests] == ["POST", "DELETE"]
    assert requests[1][1].startswith("/apis/batch/v1/namespaces/craft/jobs/notebook-run-")
    (cleanup,) = [r for r in caplog.records if r.getMessage() == "notebook run cleanup failed"]
    assert cleanup.__dict__["fields"]["error_type"] == "HTTPStatusError"
    # caplog.text leaves out each record's fields, so the whole record is searched.
    assert not [r for r in caplog.records if "generated-service-account-token" in str(r.__dict__)]
    assert "generated-service-account-token" not in str(raised.value)

    def lost(request):
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            raise httpx.ReadTimeout("no answer", request=request)
        return httpx.Response(200, json={})

    requests.clear()
    cluster.transport = httpx.MockTransport(lost)
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(notebook_jobs.JobRunner(cluster, "image", uuid4()).run("", "nb.ipynb", "o"))
    assert [method for method, _ in requests] == ["POST", "DELETE"]
