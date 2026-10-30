"""The worker heartbeat check, as the probes run it: by path, fast, and without the app; the
worker's wait for migrations; and the chart's pods around their workers."""

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from functools import cache
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.infrastructure import heartbeat, queue
from app.infrastructure.paths import ROOT

MODULE = ROOT / "app/infrastructure/heartbeat.py"
# Where the image puts the module (app/Dockerfile copies the repository's app/ to /app/app).
IN_IMAGE = "/app/" + MODULE.relative_to(ROOT).as_posix()


def probe(tmp_path: Path) -> int:
    """Run the module as the container probes do, with the heartbeat folder at tmp_path."""
    env = {"PATH": os.environ.get("PATH", ""), "TMPDIR": str(tmp_path)}
    return subprocess.run([sys.executable, "-I", "-S", str(MODULE)], env=env, timeout=30).returncode


def test_fresh_heartbeat_is_healthy_and_stale_or_missing_is_not(tmp_path, monkeypatch):
    beat = tmp_path / "craft-worker-heartbeat"
    monkeypatch.setattr(heartbeat, "FILE", beat)
    assert not heartbeat.healthy() and not queue.check_worker()
    beat.touch()
    assert heartbeat.healthy() and queue.check_worker()
    old = time.time() - heartbeat.HEALTHY_SECONDS - 1
    os.utime(beat, (old, old))
    assert not heartbeat.healthy() and not queue.check_worker()


def test_run_by_path_exits_zero_only_for_a_fresh_heartbeat(tmp_path):
    assert probe(tmp_path) == 1
    beat = tmp_path / "craft-worker-heartbeat"
    beat.touch()
    assert probe(tmp_path) == 0
    old = time.time() - heartbeat.HEALTHY_SECONDS - 1
    os.utime(beat, (old, old))
    assert probe(tmp_path) == 1


def test_run_by_path_imports_only_the_standard_library():
    # What the probe loads: anything outside the standard library (above all, the app
    # package with its model SDKs) would make every probe slow again.
    script = (
        "import runpy, sys\n"
        "before = set(sys.modules)\n"
        f"runpy.run_path({str(MODULE)!r}, run_name='probe')\n"
        "names = {name.split('.')[0] for name in set(sys.modules) - before}\n"
        "print(sorted(names - set(sys.stdlib_module_names)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def probe_blocks(rendered: str) -> list[str]:
    """Each startup, liveness or readiness probe block of a rendered manifest, as text."""
    lines = rendered.splitlines()
    blocks = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)(startup|liveness|readiness)Probe:", line)
        if match is None:
            continue
        indent = len(match[1])
        block = [line]
        for following in lines[index + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            block.append(following)
        blocks.append("\n".join(block))
    return blocks


LOCAL = ("-f", str(ROOT / "deploy/local.yaml"))
needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


@cache
def helm_template(*values: str) -> str:
    """The chart as Helm renders it, with these values options (none: the chart's defaults)."""
    return subprocess.run(
        ["helm", "template", "craft", str(ROOT / "deploy/craft"), *values]
        + ["--set", "provider.hostPath=/tmp/provider"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout


@needs_helm
def test_chart_probes_never_start_the_cli_and_always_set_a_timeout():
    rendered = helm_template(*LOCAL)
    blocks = probe_blocks(rendered)
    worker = [block for block in blocks if IN_IMAGE in block]
    # A startup and a liveness probe for each worker container (craft.worker in _helpers.tpl).
    containers = rendered.count("command: [craft, worker]")
    assert containers > 0 and len(worker) == 2 * containers
    for block in blocks:
        assert "timeoutSeconds:" in block, block
        assert "craft, worker" not in block and "--check" not in block, block
    for block in worker:
        assert "[python, -I, -S, " + IN_IMAGE + "]" in block, block


def test_compose_worker_healthcheck_runs_the_same_check():
    compose = (ROOT / "compose.yaml").read_text()
    assert f'["CMD", "python", "-I", "-S", "{IN_IMAGE}"]' in compose
    assert '"craft", "worker", "--check"' not in compose


def manifests(values: tuple[str, ...]) -> dict[tuple[str, str], dict]:
    documents = yaml.safe_load_all(helm_template(*values))
    return {(doc["kind"], doc["metadata"]["name"]): doc for doc in documents if doc}


@needs_helm
@pytest.mark.parametrize("values", [LOCAL, ()], ids=["local", "defaults"])
def test_chart_runs_craft_id_beside_the_api_which_outlives_its_workers(values):
    rendered = manifests(values)
    pod = rendered["Deployment", "craft-api"]["spec"]["template"]
    # A sidecar: up before the main containers (the in-pod workers) start, stopped after them.
    assert [(c["name"], c["restartPolicy"]) for c in pod["spec"]["initContainers"]] == [
        ("api", "Always")
    ]
    # craft-id on the pod's localhost, where the API finds its issuer, as compose shares one.
    assert "id" in [container["name"] for container in pod["spec"]["containers"]]
    assert rendered["Service", "craft-id"]["spec"]["selector"] == pod["metadata"]["labels"]
    assert ("Deployment", "craft-id") not in rendered


def test_workspaces_reach_the_api_pod_but_not_craft_id_beside_it():
    # H8, with craft-id in the API pod: a notebook reaches the API (8000) and the run bridge
    # (an ephemeral port), never craft-id's sign-in form (8100).
    hub = yaml.safe_load((ROOT / "deploy/jupyterhub/values.yaml").read_text())
    (rule,) = [
        rule
        for rule in hub["singleuser"]["networkPolicy"]["egress"]
        if rule["to"] == [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "api"}}}]
    ]
    ranges = [(port["port"], port.get("endPort", port["port"])) for port in rule["ports"]]
    reaches = [
        any(low <= port <= high for low, high in ranges) for port in (8000, 32768, 60999, 8100)
    ]
    assert reaches == [True, True, True, False]


@needs_helm
@pytest.mark.parametrize("values", [LOCAL, ()], ids=["local", "defaults"])
def test_chart_pods_outlast_their_workers_grace_and_request_cpu(values):
    served = set()
    for (kind, name), document in manifests(values).items():
        if kind not in {"Deployment", "StatefulSet"}:
            continue
        pod = document["spec"]["template"]["spec"]
        containers = pod.get("initContainers", []) + pod["containers"]
        for container in containers:
            assert container["resources"]["requests"].get("cpu"), (name, container["name"])
        queues = {
            queue_name
            for container in containers
            for variable in container.get("env", [])
            if variable["name"] == "CRAFT_WORKER_QUEUES"
            for queue_name in variable["value"].split(",")
        }
        if queues:
            # Procrastinate stops waiting at GRACE, then aborts; SIGKILL must come after that.
            grace = max(queue.GRACE[queue_name] for queue_name in queues)
            assert pod["terminationGracePeriodSeconds"] > grace, name
            served |= queues
    assert served == set(queue.GRACE)


def test_a_worker_waits_until_the_database_is_at_the_newest_migration(
    settings, monkeypatch, tmp_path
):
    from app.health import handlers as health

    class Waited(Exception):
        pass

    def sleep(seconds):
        raise Waited()

    head = health.migration_head
    monkeypatch.setattr(heartbeat, "FILE", tmp_path / "craft-worker-heartbeat")
    # The pytest process keeps its own SIGTERM handling.
    monkeypatch.setattr(
        queue, "signal", SimpleNamespace(signal=lambda *_: None, SIGTERM=signal.SIGTERM)
    )
    monkeypatch.setattr(queue, "time", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(health, "migration_head", lambda: "a newer migration")
    with pytest.raises(Waited):
        queue.wait_for_schema(settings)
    assert heartbeat.healthy()  # healthy while it waits
    monkeypatch.setattr(health, "migration_head", head)
    queue.wait_for_schema(settings)  # returns at once: the test database is at the head
