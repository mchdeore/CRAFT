"""Notebook runs as Jobs on the local cluster (deploy/README.md), started by the real worker.

Run with ``uv run pytest -m cluster app/workspace/tests/test_notebook_jobs_cluster.py`` after
building both images and upgrading both charts. Each test makes a throwaway person volume.
"""

import json
import subprocess
import time
import uuid

import pytest

pytestmark = pytest.mark.cluster

IMAGE = "craft-user-workspace:local"
ACCOUNT = "system:serviceaccount:default:craft-notebook-runs"
RUN = """
import asyncio, sys, time, uuid
from app.infrastructure import notebook_jobs as jobs

async def main(user, cancel_after):
    run = jobs.run(jobs.in_cluster(), IMAGE, uuid.UUID(user), "work", "nb.ipynb", "nb.run.ipynb")
    if not cancel_after:
        return await run
    task = asyncio.ensure_future(run)
    await asyncio.sleep(cancel_after)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

asyncio.run(main(sys.argv[1], float(sys.argv[2])))
""".replace("IMAGE", repr(IMAGE))
CONNECT = (
    "import socket, sys\n"
    "try:\n"
    "    socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3)\n"
    "    print('online')\n"
    "except OSError:\n    print('offline')\n"
)


def kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, check=check)


def notebook(*sources: str) -> str:
    cells = [
        {"cell_type": "code", "metadata": {}, "source": s, "outputs": [], "execution_count": None}
        for s in sources
    ]
    spec = {"name": "python3", "display_name": "Python 3", "language": "python"}
    return json.dumps(
        {"cells": cells, "metadata": {"kernelspec": spec}, "nbformat": 4, "nbformat_minor": 5}
    )


@pytest.fixture
def person():
    """A person volume with a helper pod that reads and writes it."""
    user = str(uuid.uuid4())
    claim, helper = f"claim-{user}", f"files-{user[:8]}"
    pvc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": claim},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": "local-path",
            "resources": {"requests": {"storage": "100Mi"}},
        },
    }
    overrides = {
        "spec": {
            "securityContext": {"runAsUser": 1000, "runAsGroup": 1000},
            "volumes": [{"name": "v", "persistentVolumeClaim": {"claimName": claim}}],
            "containers": [
                {
                    "name": "files",
                    "image": IMAGE,
                    "imagePullPolicy": "Never",
                    "command": ["sleep", "3600"],
                    "volumeMounts": [{"name": "v", "mountPath": "/workspace"}],
                }
            ],
        }
    }
    subprocess.run(["kubectl", "apply", "-f", "-"], input=json.dumps(pvc), text=True, check=True)
    kubectl(
        "run", helper, f"--image={IMAGE}", "--restart=Never", f"--overrides={json.dumps(overrides)}"
    )
    kubectl("wait", "--for=condition=Ready", f"pod/{helper}", "--timeout=120s")
    # local-path makes the folder root-owned; the person's server would own it.
    kubectl("exec", helper, "--", "sh", "-c", "test -w /workspace")

    def write(path: str, content: str) -> None:
        code = (
            "import pathlib, sys; p = pathlib.Path('/workspace', sys.argv[1]);"
            "p.parent.mkdir(parents=True, exist_ok=True); p.write_text(sys.argv[2])"
        )
        kubectl("exec", helper, "--", "python", "-c", code, path, content)

    def files() -> dict[str, str]:
        code = (
            "import json,pathlib; r=pathlib.Path('/workspace');"
            "print(json.dumps({str(p.relative_to(r)): p.read_text()"
            " for p in r.rglob('*') if p.is_file()}))"
        )
        return json.loads(kubectl("exec", helper, "--", "python", "-c", code).stdout)

    def run(cancel_after: float = 0) -> subprocess.CompletedProcess:
        return kubectl(
            "exec", "deploy/craft-worker-interactive", "--",
            "python", "-c", RUN, user, str(cancel_after), check=False,
        )  # fmt: skip

    yield {"user": user, "helper": helper, "write": write, "files": files, "run": run}
    kubectl("delete", "pod", helper, "--now", check=False)
    kubectl("delete", "pvc", claim, "--wait=false", check=False)


def test_a_run_is_offline_read_only_and_moves_results_back_without_overwriting(person):
    escape = (
        "import pathlib\ntry:\n    pathlib.Path('escape.txt').write_text('x'); print('wrote')\n"
        "except OSError:\n    print('read-only')\n"
    )
    person["write"]("work/outputs/keep.txt", "keep")
    person["write"]("work/outputs/changed.txt", "a")
    person["write"](
        "work/nb.ipynb",
        notebook(
            # With the kernel's own guard set aside, only the network policy is left.
            "import craft_guard, sys\ncraft_guard._local.bypass = True\n"
            "sys.argv = ['', '10.43.0.1', '443']\n" + CONNECT,
            escape,
            "import pathlib\npathlib.Path('outputs/answer.txt').write_text('42')\n"
            "pathlib.Path('outputs/changed.txt').write_text('b')",
        ),
    )
    # The same connection from an ordinary pod works, so the run's failure is its policy.
    control = kubectl(
        "exec", person["helper"], "--", "python", "-I", "-S", "-c", CONNECT, "10.43.0.1", "443"
    )
    assert control.stdout.strip() == "online"

    done = person["run"]()
    assert done.returncode == 0, done.stderr[-2000:]
    found = person["files"]()
    executed = json.loads(found["work/nb.run.ipynb"])
    printed = ["".join(o.get("text", "")) for c in executed["cells"] for o in c["outputs"]]
    assert printed[0].strip() == "offline" and printed[1].strip() == "read-only"
    assert found["work/outputs/answer.txt"] == "42"
    assert found["work/outputs/keep.txt"] == "keep" and found["work/outputs/changed.txt"] == "a"
    assert [v for k, v in found.items() if k.startswith("work/outputs/changed.run-")] == ["b"]
    assert not any(k.startswith("work/outputs/keep.run-") for k in found)
    assert "work/escape.txt" not in found and not any(k.startswith(".craft-runs/") for k in found)


def test_cancel_stops_a_running_notebook_within_five_seconds(person):
    person["write"]("work/nb.ipynb", notebook("import time; time.sleep(600)"))
    cancelled = person["run"](cancel_after=20)
    stopped = time.monotonic()
    assert cancelled.returncode == 0, cancelled.stderr[-2000:]
    while kubectl("get", "pods", "-l", "craft-notebook-run=true", "-o", "name").stdout.strip():
        assert time.monotonic() - stopped < 5
        time.sleep(0.2)
    assert "work/nb.run.ipynb" not in person["files"]()


def test_the_worker_may_only_manage_run_jobs():
    allowed = kubectl("auth", "can-i", "--list", f"--as={ACCOUNT}").stdout
    rows = [r for r in allowed.splitlines()[1:] if r.split()[0].endswith("jobs.batch")]
    assert rows and all("[create get delete]" in r for r in rows)
    for verb, resource in (
        ("get", "pods"),
        ("get", "secrets"),
        ("list", "jobs"),
        ("create", "pods"),
    ):
        assert (
            kubectl("auth", "can-i", verb, resource, f"--as={ACCOUNT}", check=False).stdout.strip()
            == "no"
        )
    # A Job of the run's shape on another volume, such as the database's, is refused.
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "not-a-run"},
        "spec": {
            "template": {
                "metadata": {"labels": {"craft-notebook-run": "true"}},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": "x",
                            "image": IMAGE,
                            "securityContext": {
                                "runAsNonRoot": True,
                                "allowPrivilegeEscalation": False,
                            },
                        }
                    ],
                    "volumes": [
                        {
                            "name": "d",
                            "persistentVolumeClaim": {"claimName": "data-craft-postgres-0"},
                        }
                    ],
                },
            }
        },
    }
    refused = subprocess.run(
        ["kubectl", "create", f"--as={ACCOUNT}", "-f", "-"],
        input=json.dumps(job), capture_output=True, text=True,
    )  # fmt: skip
    assert refused.returncode != 0 and "person's volume" in refused.stderr


def test_only_crafts_pods_reach_the_database(person):
    outside = kubectl(
        "exec",
        person["helper"],
        "--",
        "python",
        "-I",
        "-S",
        "-c",
        CONNECT,
        "craft-postgres",
        "5432",
    )
    inside = kubectl(
        "exec",
        "deploy/craft-worker-interactive",
        "--",
        "python",
        "-I",
        "-S",
        "-c",
        CONNECT,
        "craft-postgres",
        "5432",
    )
    assert outside.stdout.strip() == "offline" and inside.stdout.strip() == "online"
