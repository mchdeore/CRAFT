"""Run one notebook unattended in a fresh Docker container: no network, read-only root, bounded.

Compose's ``NotebookRunner`` (``notebook_runs.py``) until K-remove-docker deletes this module.
The worker copies the notebook's folder in through the owner's Jupyter server, runs it with
nbclient as a non-root user, and copies the executed notebook and its ``outputs/`` folder back
beside it without overwriting. Killing the container stops a run at once; the owner's kernel is
never touched.
"""

import asyncio
import io
import logging
import tarfile
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from app.infrastructure import workspaces
from app.infrastructure.notebook_runs import CELL_SECONDS, TOTAL_SECONDS, RunError

logger = logging.getLogger("craft.workspace")

FOLDER_LIMIT = 50 * 1024 * 1024
HOME = "/home/workspace"
WORK = f"{HOME}/run"

RUNNER = """
import sys, nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError
name, output, seconds = sys.argv[1], sys.argv[2], int(sys.argv[3])
notebook = nbformat.read(name, as_version=4)
client = NotebookClient(notebook, timeout=seconds, kernel_name="python3",
                        resources={"metadata": {"path": "."}})
status = 0
try:
    client.execute()
except CellExecutionError:
    status = 3
nbformat.write(notebook, output)
sys.exit(status)
"""


@dataclass
class SandboxRunner:
    """One owner's notebook runs, through their running Jupyter server ``target``."""

    target: workspaces.JupyterTarget
    image: str

    async def run(self, folder: str, notebook: str, output: str) -> None:
        """Copy the folder in, run, and write new results beside the notebook.

        A result whose name CRAFT can't write (a hidden file such as ``outputs/.cache/x``) is
        skipped and counted in the log; every other result is still written.
        """
        try:
            files = await workspaces.read_folder(self.target, folder, FOLDER_LIMIT)
        except ValueError:  # read_folder's one refusal: the folder is over FOLDER_LIMIT
            raise RunError("folder_too_large") from None
        sandbox = Sandbox(self.image)
        try:
            code, made = await asyncio.to_thread(sandbox.run, files, notebook, output)
        finally:
            # A cancelled job lands here first: removing the container stops the run at once.
            sandbox.kill()
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        before = {_join(folder, key) for key in files}
        skipped = 0
        for relative, content in sorted(made.items()):
            if relative.split("/", 1)[0] == "outputs" and files.get(relative) == content:
                continue  # an output the notebook did not change
            # Names come out of the container, so each is checked like a request path.
            destination = _owned(_free(_join(folder, relative), before, stamp))
            # Never overwrite: an existing file keeps its content and the run's copy is renamed.
            if destination and await workspaces.exists(self.target, destination):
                renamed = _free(destination, {destination}, f"{stamp}-{uuid4().hex[:6]}")
                destination = _owned(renamed)
            if destination is None:
                skipped += 1
                continue
            await workspaces.write_bytes(self.target, destination, content)
        if skipped:
            logger.info("notebook run skipped results", extra={"fields": {"skipped": skipped}})
        if code not in (0, 3):  # 3: a cell raised; the executed copy shows the error
            raise RunError("run_failed")


def _join(folder: str, name: str) -> str:
    return f"{folder}/{name}".lstrip("/")


def _owned(name: str) -> str | None:
    """``name`` if CRAFT may write it in the owner's workspace; None for a hidden or bad path."""
    try:
        return workspaces.owned_name(name)
    except ValueError:
        return None


def _free(name: str, taken: set[str], stamp: str) -> str:
    """The name itself, or name.run-<stamp>.ext beside it when that file already exists."""
    if name not in taken:
        return name
    path = PurePosixPath(name)
    return str(path.with_name(f"{path.stem}.run-{stamp}{path.suffix}"))


@dataclass
class Sandbox:
    """One container for one run; ``kill`` may be called from another thread."""

    image: str
    client: Any = None
    container: Any = None
    volume: Any = None
    killed: threading.Event = field(default_factory=threading.Event)

    def api(self) -> Any:
        if self.client is None:
            import docker

            self.client = docker.from_env(timeout=30)
        return self.client

    def run(self, files: dict[str, bytes], notebook: str, output: str) -> tuple[int, dict]:
        """Execute ``notebook`` (relative to ``files``); returns its exit code and new files."""
        pinned = self.api().images.get(self.image).id  # the run uses this exact image
        # The run folder is its own memory-backed volume, owned by the runner and bounded:
        # Docker cannot copy files into a plain tmpfs of a container with a read-only root.
        self.volume = self.api().volumes.create(
            driver="local",
            driver_opts={"type": "tmpfs", "device": "tmpfs", "o": "size=1g,uid=1000,gid=1000"},
            labels={"craft.notebook-run": "1"},
        )
        self.container = self.api().containers.create(
            pinned,
            ["python", "-c", "import time; time.sleep(86400)"],
            user="1000:1000",
            network_mode="none",
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit=2 * 1024**3,
            nano_cpus=2_000_000_000,
            pids_limit=256,
            tmpfs={
                "/tmp": "rw,nosuid,size=256m,uid=1000,gid=1000",
                HOME: "rw,nosuid,size=1g,uid=1000,gid=1000",
            },
            environment={"USER_ROOT": WORK, "HOME": HOME},
            volumes={self.volume.name: {"bind": WORK, "mode": "rw"}},
            working_dir="/",
            init=True,
            labels={"craft.notebook-run": "1"},
        )
        try:
            self.container.start()
            if not self.container.put_archive(WORK, _tar(files)):
                raise RunError("copy_failed")
            if self.killed.is_set():
                raise RunError("cancelled")
            code, _ = self.container.exec_run(
                [
                    "timeout",
                    str(TOTAL_SECONDS),
                    "python",
                    "-c",
                    RUNNER,
                    notebook,
                    output,
                    str(CELL_SECONDS),
                ],
                user="1000:1000",
                workdir=f"{WORK}/{notebook.rpartition('/')[0]}".rstrip("/"),
            )
            if self.killed.is_set():
                raise RunError("cancelled")
            if code == 124:
                raise RunError("run_timeout")
            folder = notebook.rpartition("/")[0]
            prefix = f"{folder}/" if folder else ""
            made = {}
            for wanted in (f"{prefix}{output}", f"{prefix}outputs"):
                try:
                    stream, _ = self.container.get_archive(f"{WORK}/{wanted}")
                except Exception:
                    continue
                for name, content in _untar(b"".join(stream)).items():
                    base = wanted.rpartition("/")[0]
                    made[f"{base}/{name}" if base else name] = content
            return code, made
        finally:
            self.kill()

    def kill(self) -> None:
        """Stop and remove the container; safe to call from another thread, more than once."""
        self.killed.set()
        container, self.container = self.container, None
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass
        volume, self.volume = self.volume, None
        if volume is not None:
            try:
                volume.remove(force=True)
            except Exception:
                pass


def _tar(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        # The run folder itself first, so the unprivileged runner owns it and can write there.
        folders = {"."} | {
            "/".join(name.split("/")[:depth])
            for name in files
            for depth in range(1, name.count("/") + 1)
        }
        for folder in sorted(folders):
            info = tarfile.TarInfo(folder)
            info.type, info.uid, info.gid, info.mode = tarfile.DIRTYPE, 1000, 1000, 0o755
            archive.addfile(info)
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.uid, info.gid, info.mode = len(content), 1000, 1000, 0o644
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _untar(data: bytes) -> dict[str, bytes]:
    found = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            parts = member.name.split("/")
            if not member.isfile() or any(part in {"", ".", ".."} for part in parts):
                continue
            extracted = archive.extractfile(member)
            if extracted is not None:
                found[member.name] = extracted.read()
    return found
