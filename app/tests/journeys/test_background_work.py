"""Put a notebook in the workspace and run it in the background: the run waits in the queue
until a worker takes it, and once it finishes the bell shows it until the person marks it read.
They stop a second run while it runs, and run the notebook again after a run fails. A stranger
can't see or touch the runs or the bell."""

import json
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.infrastructure import hub, notebook_jobs, queue, workspaces
from app.tests.journeys.support import worker
from app.tests.support.chat import wait_for

NOTEBOOK = {
    "cells": [
        {"cell_type": "code", "metadata": {}, "source": "1", "outputs": [], "execution_count": None}
    ],
    "metadata": {},
    "nbformat": 4,
    "nbformat_minor": 5,
}
ENDED = {"finished", "failed", "canceled", "stopped"}


@pytest.fixture
def notebook_runs(monkeypatch) -> dict:
    """A ready workspace whose files live in a dict, and the cluster the worker runs notebooks
    on. Each run's Job ends as notebook_runs["end"] says: "Complete", "Failed", or None to keep
    running until it is deleted; notebook_runs["deleted"] counts the Jobs deleted."""
    files: dict[str, str] = {}
    state = {"end": "Complete", "deleted": 0}

    async def ready(self, user_id):
        return "ready"

    async def write_text(target, path, content):
        files[path] = content
        return {}

    async def read_notebook(target, path):
        return json.loads(files[path]) if path in files else None

    def kubernetes(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={})
        if request.method == "DELETE":
            state["deleted"] += 1
            return httpx.Response(200, json={})
        if state["end"] is None:
            return httpx.Response(200, json={"status": {"active": 1}})
        ended = {"type": state["end"], "status": "True"}
        return httpx.Response(200, json={"status": {"conditions": [ended]}})

    cluster = notebook_jobs.Cluster(
        "https://k8s.test", "craft", {}, transport=httpx.MockTransport(kubernetes)
    )
    # A Hub call the fakes miss fails at once on a closed port.
    monkeypatch.setattr(
        hub, "from_settings", lambda _: hub.Hub("http://127.0.0.1:1", "", "generated-hub-token")
    )
    monkeypatch.setattr(hub.Hub, "state", ready)
    monkeypatch.setattr(workspaces, "write_text", write_text)
    monkeypatch.setattr(workspaces, "read_notebook", read_notebook)
    monkeypatch.setattr(notebook_jobs, "in_cluster", lambda: cluster)  # asked on the worker
    monkeypatch.setattr(notebook_jobs, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(queue, "POLL_SECONDS", 0.1)  # in case the worker misses a NOTIFY
    return state


def queue_run(http: TestClient) -> dict:
    """Queue a run of the notebook; it returns at once."""
    queued = http.post("/api/workspace/notebooks/runs", json={"path": "work/fit.ipynb"})
    assert queued.status_code == 200
    return queued.json()


def read_job(http: TestClient, job: str) -> dict:
    """The job as its page shows it."""
    found = http.get(f"/api/jobs/{job}")
    assert found.status_code == 200
    return found.json()


def polled(http: TestClient, job: str, *states: str) -> dict:
    """The job, read again as its page polls it, once it is in one of `states` or has ended."""
    wait_for(lambda: read_job(http, job)["status"] in {*states, *ENDED})
    return read_job(http, job)


def unread(http: TestClient) -> list[dict]:
    """The bell: the person's unread notifications, newest first."""
    found = http.get("/api/notifications", params={"unread": True})
    assert found.status_code == 200
    return found.json()


def test_a_person_runs_a_notebook_in_the_background_stops_a_run_and_retries_a_failed_one(
    journey, notebook_runs, settings
) -> None:
    http = journey.http
    assert http.get("/api/notifications").status_code == 401
    journey.sign_in()

    # They put a notebook in their workspace and queue a run of it.
    content = json.dumps(NOTEBOOK)
    saved = http.put("/api/workspace/files", json={"path": "work/fit.ipynb", "content": content})
    assert saved.status_code == 200
    assert saved.json() == {"path": "work/fit.ipynb", "size": len(content.encode())}
    run = queue_run(http)
    first = run["jobId"]
    assert run == {"jobId": first, "notebook": "work/fit.ipynb", "output": run["output"]}
    # The executed copy will land beside the notebook.
    assert run["output"].startswith("work/fit.run-") and run["output"].endswith(".ipynb")

    # No worker has taken it yet: it waits in the queue, and the bell is quiet.
    queued = read_job(http, first)
    assert queued == {
        "jobId": first,
        "kind": "workspace.notebook_run",
        "queue": "interactive",
        "status": "queued",
        "attempts": 0,
        "requestedBy": str(journey.person.id),
        "enqueuedAt": queued["enqueuedAt"],
        "startedAt": None,
        "finishedAt": None,
        "error": None,
        "progress": None,
    }
    assert http.get("/api/notifications").json() == []

    # A second person can neither read the run nor cancel it.
    journey.as_stranger(("GET", f"/api/jobs/{first}"), ("POST", f"/api/jobs/{first}/cancel"))
    assert read_job(http, first) == queued

    # The worker stops only after every run has ended: stopping it sooner would cut a run short.
    with worker(journey.owner, settings, ["interactive"]):
        # A worker takes the run, and it finishes.
        done = polled(http, first)
        assert done == queued | {
            "status": "finished",
            "attempts": 1,
            "startedAt": done["startedAt"],
            "finishedAt": done["finishedAt"],
        }
        times = [done[key] for key in ("enqueuedAt", "startedAt", "finishedAt")]
        assert times == sorted(times, key=datetime.fromisoformat)

        # The bell rings for it. A second person can't mark it read; the person does, and
        # marking it again changes nothing.
        [bell] = unread(http)
        assert bell == {
            "id": bell["id"],
            "kind": "job_done",
            "refKind": "job",
            "refId": first,
            "createdAt": bell["createdAt"],
            "readAt": None,
        }
        mark = f"/api/notifications/{bell['id']}/read"
        journey.as_stranger(("POST", mark))
        assert unread(http) == [bell]
        marked = http.post(mark)
        assert marked.status_code == 200
        assert marked.json() == bell | {"readAt": marked.json()["readAt"]}
        assert marked.json()["readAt"] is not None
        again = http.post(mark)
        assert again.status_code == 200 and again.json() == marked.json()
        assert unread(http) == []

        # A second run keeps going until they stop it. Its Job is deleted, which ends it, and
        # stopping their own run doesn't ring the bell.
        notebook_runs["end"] = None
        second = queue_run(http)["jobId"]
        assert polled(http, second, "started")["status"] == "started"
        stopping = http.post(f"/api/jobs/{second}/cancel")
        assert stopping.status_code == 200
        assert (stopping.json()["status"], stopping.json()["finishedAt"]) == ("stopping", None)
        stopped = polled(http, second)
        assert (stopped["status"], stopped["attempts"], stopped["error"]) == ("stopped", 1, None)
        assert stopped["finishedAt"] is not None
        assert notebook_runs["deleted"] == 2  # the finished run's Job, and the stopped run's
        twice = http.post(f"/api/jobs/{second}/cancel")
        assert twice.status_code == 409 and twice.json()["code"] == "conflict"
        assert unread(http) == []

        # A failed run rings the bell. There is no retry route: running the notebook again is a
        # new job, and the failed one stays as it was.
        notebook_runs["end"] = "Failed"
        third = queue_run(http)["jobId"]
        failed = polled(http, third)
        assert (failed["status"], failed["attempts"], failed["error"]) == ("failed", 1, "RunError")
        assert [(row["kind"], row["refId"]) for row in unread(http)] == [("job_failed", third)]
        assert http.post(f"/api/jobs/{third}/retry").status_code == 404
        notebook_runs["end"] = "Complete"
        fourth = queue_run(http)["jobId"]
        retried = polled(http, fourth)
        assert retried["status"] == "finished"
        assert read_job(http, third) == failed
        assert [(row["kind"], row["refId"]) for row in unread(http)] == [
            ("job_done", fourth),
            ("job_failed", third),
        ]

    # Their jobs list shows each run as its own page does, newest first.
    listed = http.get("/api/jobs")
    assert listed.status_code == 200
    assert listed.json() == {"jobs": [retried, failed, stopped, done], "nextOffset": None}

    # A second person lists none of the runs and none of the bell.
    assert journey.stranger.get("/api/jobs").json() == {"jobs": [], "nextOffset": None}
    assert journey.stranger.get("/api/notifications").json() == []
