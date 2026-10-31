"""Whole-notebook runs queue a job and run as a Kubernetes Job, which never overwrites a file.

These tests cover the queue, how the service starts a ``JobRunner`` (and fails outside a
cluster) and the Job's lifecycle, including failures, cancellation and what reaches the logs.
"""

import asyncio
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest

from app.infrastructure import notebook_jobs
from app.infrastructure.notebook_jobs import RunError
from app.workspace import service
from app.workspace.handlers import RunNotebook, run_notebook

NOTEBOOK = {
    "cells": [{"cell_type": "code", "metadata": {}, "source": "1", "outputs": []}],
    "metadata": {},
    "nbformat": 4,
    "nbformat_minor": 5,
}


@pytest.fixture
def queue_run(context, workspace):
    """Queues a run of the owner's work/fit.ipynb; every queued job is deleted afterwards."""
    from sqlalchemy import text

    _, files = workspace
    files["work/fit.ipynb"] = NOTEBOOK
    own = replace(context["caller"], origin="http")
    yield lambda: asyncio.run(run_notebook(own, RunNotebook(path="work/fit.ipynb")))
    with context["owner"].begin() as connection:
        connection.execute(
            text("delete from procrastinate_jobs where args->>'requested_by' = :user"),
            {"user": str(context["caller"].user_id)},
        )


def test_a_person_can_queue_more_than_two_notebook_runs(queue_run):
    queued = [queue_run() for _ in range(3)]
    assert len({run.job_id for run in queued}) == 3
    assert all(run.notebook == "work/fit.ipynb" for run in queued)


def test_two_runs_queued_in_the_same_second_get_different_outputs(queue_run, monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    moment = datetime(2026, 10, 31, 9, 10, 17, tzinfo=UTC)
    monkeypatch.setattr(service, "datetime", SimpleNamespace(now=lambda tz: moment))
    first, second = queue_run(), queue_run()
    assert first.output != second.output
    for run in (first, second):  # named after the notebook and when it was queued, to sort
        assert run.output.startswith("work/fit.run-20261031-091017")
        assert run.output.endswith(".ipynb")


def test_the_queued_job_outlasts_the_run_jobs_deadline():
    from app.infrastructure.notebook_jobs import JOB_SECONDS, TOTAL_SECONDS
    from app.workspace.jobs import notebook_run

    job = notebook_jobs.manifest("notebook-run-1", "image", uuid4(), "", "nb.ipynb", "o")
    # The Job's deadline fires first, so a slow run is a run_timeout.
    assert TOTAL_SECONDS < job["spec"]["activeDeadlineSeconds"] < JOB_SECONDS
    queued = notebook_run.__dict__["__operation__"].job
    assert queued is not None and queued.timeout == JOB_SECONDS


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
    own = replace(context["caller"], origin="http")
    asyncio.run(service.notebook_job(own, "work/fit.ipynb", "fit.run-1.ipynb"))
    image = own.settings.notebook_image
    assert started == [(own.user_id, image, "work", "fit.ipynb", "fit.run-1.ipynb")]


def test_outside_a_cluster_a_run_fails_without_starting(context, monkeypatch):
    monkeypatch.setattr(notebook_jobs, "in_cluster", lambda: None)
    monkeypatch.setattr(notebook_jobs.JobRunner, "run", None)  # never reached
    own = replace(context["caller"], origin="http")
    with pytest.raises(RunError, match="^run_failed$"):
        asyncio.run(service.notebook_job(own, "work/fit.ipynb", "fit.run-1.ipynb"))


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
        runner = notebook_jobs.JobRunner(cluster, "image", uuid4())
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


def test_the_runner_in_the_image_keeps_the_same_run_limit_and_codes():
    import craft_run

    assert craft_run.TOTAL_SECONDS == notebook_jobs.TOTAL_SECONDS
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
