"""Run one notebook unattended as a Kubernetes Job: offline, read-only root, bounded.

The Job mounts the owner's volume (JupyterHub's ``claim-<user ID>``) and runs three
containers from the workspace image (``craft_run``): a trusted step makes a run folder, the
notebook runs with its folder read-only and only the run folder writable, and a trusted step
moves the executed copy and new outputs beside the notebook without overwriting. No file
passes through the worker. Deleting the Job stops a run at once.

The worker's service account may only create, read and delete Jobs, and an admission policy
(``deploy/craft/templates/notebook-runs.yaml``) holds those Jobs to this shape.

``JobRunner`` is the cluster's ``NotebookRunner`` (``notebook_runs.py``); nothing here depends
on the Docker sandbox.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.infrastructure.notebook_runs import STEP_SECONDS, TOTAL_SECONDS, RunError

logger = logging.getLogger("craft.workspace")

ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
LABEL = "craft-notebook-run"
POLL_SECONDS = 1.0
USER = 1000


@dataclass
class Cluster:
    """Where the Kubernetes API is and how to authenticate to it."""

    base_url: str
    namespace: str
    headers: dict[str, str] = field(repr=False)  # the service account token
    verify: Any = True
    transport: httpx.AsyncBaseTransport | None = None  # tests

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            verify=self.verify,
            timeout=10,
            transport=self.transport,
        )

    def jobs(self, name: str = "") -> str:
        return f"/apis/batch/v1/namespaces/{self.namespace}/jobs/{name}".rstrip("/")


def in_cluster() -> Cluster | None:
    """The worker's own cluster, when it runs in one with a service account; else None."""
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    if not host or not (ACCOUNT / "token").is_file():
        return None
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    return Cluster(
        base_url=f"https://{host}:{port}",
        namespace=(ACCOUNT / "namespace").read_text().strip(),
        headers={"Authorization": f"Bearer {(ACCOUNT / 'token').read_text().strip()}"},
        verify=str(ACCOUNT / "ca.crt"),
    )


def manifest(
    name: str, image: str, user_id: UUID, folder: str, notebook: str, output: str
) -> dict[str, Any]:
    """The Job for one run: prepare, run (offline, folder read-only) and collect."""
    run_folder = f".craft-runs/{name}"
    env = [
        {"name": "RUN", "value": name},
        {"name": "FOLDER", "value": folder},
        {"name": "NOTEBOOK", "value": notebook},
        {"name": "OUTPUT", "value": output},
        {"name": "HOME", "value": "/home/workspace"},
    ]
    security = {
        "runAsUser": USER,
        "runAsGroup": USER,
        "runAsNonRoot": True,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    scratch = [
        {"name": "tmp", "mountPath": "/tmp"},
        {"name": "home", "mountPath": "/home/workspace"},
    ]

    def step(step_name: str, mounts: list[dict[str, Any]], memory: str, cpu: str):
        return {
            "name": step_name,
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            "command": ["python", "-m", "craft_run", step_name],
            "env": [*env, {"name": "USER_ROOT", "value": "/work"}],
            "workingDir": "/work" if step_name == "run" else "/workspace",
            "volumeMounts": [*mounts, *scratch],
            "securityContext": security,
            "resources": {
                "limits": {"memory": memory, "cpu": cpu},
                "requests": {"memory": "128Mi", "cpu": "100m"},
            },
        }

    volume = [{"name": "volume", "mountPath": "/workspace"}]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": {LABEL: "true"}},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": TOTAL_SECONDS + 2 * STEP_SECONDS,
            "ttlSecondsAfterFinished": 600,  # the worker deletes it; this catches a lost worker
            "template": {
                "metadata": {"labels": {LABEL: "true"}},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": 1,
                    "securityContext": {"runAsNonRoot": True},
                    "initContainers": [
                        step("prepare", volume, "256Mi", "500m"),
                        step(
                            "run",
                            [
                                {
                                    "name": "volume",
                                    "mountPath": "/work",
                                    "subPath": folder,
                                    "readOnly": True,
                                },
                                {
                                    "name": "volume",
                                    "mountPath": "/work/outputs",
                                    "subPath": f"{run_folder}/outputs",
                                },
                                {"name": "volume", "mountPath": "/out", "subPath": run_folder},
                            ],
                            "2Gi",
                            "2",
                        ),
                    ],
                    "containers": [step("collect", volume, "256Mi", "500m")],
                    "volumes": [
                        {
                            "name": "volume",
                            "persistentVolumeClaim": {"claimName": f"claim-{user_id}"},
                        },
                        {"name": "tmp", "emptyDir": {"sizeLimit": "256Mi"}},
                        {"name": "home", "emptyDir": {"sizeLimit": "1Gi"}},
                    ],
                },
            },
        },
    }


@dataclass
class JobRunner:
    """One owner's notebook runs, as Jobs in ``cluster`` from the workspace ``image``."""

    cluster: Cluster
    image: str
    owner: UUID

    async def run(self, folder: str, notebook: str, output: str) -> None:
        """Create the Job and wait; a cancelled wait deletes the Job, which stops the run."""
        name = f"notebook-run-{uuid4().hex[:12]}"
        body = manifest(name, self.image, self.owner, folder, notebook, output)
        async with self.cluster.http() as http:
            try:
                created = await http.post(self.cluster.jobs(), json=body)
                created.raise_for_status()
                while True:
                    found = await http.get(self.cluster.jobs(name))
                    found.raise_for_status()
                    conditions = {
                        c["type"]: c
                        for c in found.json().get("status", {}).get("conditions") or []
                        if c.get("status") == "True"
                    }
                    if "Complete" in conditions:
                        return
                    if "Failed" in conditions:
                        if conditions["Failed"].get("reason") == "DeadlineExceeded":
                            raise RunError("run_timeout")
                        raise RunError("run_failed")
                    await asyncio.sleep(POLL_SECONDS)
            finally:
                # Also after a cancelled or lost create: deleting by name stops whatever exists.
                await asyncio.shield(_delete(http, self.cluster, name))


async def _delete(http: httpx.AsyncClient, cluster: Cluster, name: str) -> None:
    """Delete the Job and its pod; already gone is fine, and a failure is only logged."""
    try:
        gone = await http.delete(cluster.jobs(name), params={"propagationPolicy": "Background"})
        if gone.status_code != 404:
            gone.raise_for_status()
    except Exception as error:
        logger.warning(
            "notebook run cleanup failed",
            extra={"fields": {"name": name, "error_type": type(error).__name__}},
        )
