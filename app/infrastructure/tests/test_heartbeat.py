"""The worker heartbeat check, as the probes run it: by path, fast, and without the app."""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

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


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_chart_probes_never_start_the_cli_and_always_set_a_timeout():
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "craft",
            str(ROOT / "deploy/craft"),
            "-f",
            str(ROOT / "deploy/local.yaml"),
            "--set",
            "provider.hostPath=/tmp/provider",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
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
