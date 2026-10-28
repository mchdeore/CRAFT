"""Run one notebook unattended in a fresh container: no network, read-only root, bounded.

The worker copies the notebook's folder in, runs it with nbclient as a non-root user,
and copies the executed notebook and its outputs/ folder back out. Killing the
container stops a run at once; the owner's workspace and kernel are never touched.
"""

import io
import tarfile
import threading
from dataclasses import dataclass, field
from typing import Any

FOLDER_LIMIT = 50 * 1024 * 1024
CELL_SECONDS = 120
TOTAL_SECONDS = 15 * 60
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


class SandboxError(RuntimeError):
    """Only fixed codes cross this boundary."""


@dataclass
class Sandbox:
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
        if sum(len(content) for content in files.values()) > FOLDER_LIMIT:
            raise SandboxError("folder_too_large")
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
                raise SandboxError("copy_failed")
            if self.killed.is_set():
                raise SandboxError("cancelled")
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
                raise SandboxError("cancelled")
            if code == 124:
                raise SandboxError("run_timeout")
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
