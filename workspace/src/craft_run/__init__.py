"""The three steps of an unattended notebook run, each a container of one Kubernetes Job.

``prepare`` and ``collect`` are trusted: they see the person's whole volume, read-write.
``run`` executes the notebook with the notebook's folder read-only and only the run folder
writable, offline. Files never leave the volume: ``collect`` moves the executed copy and
new or changed outputs beside the notebook and never overwrites a file.

Paths come from the environment: ``RUN`` (the run's name), ``FOLDER`` (the notebook's
folder, "" for the top), ``NOTEBOOK`` (its file name) and ``OUTPUT`` (the executed copy's).
"""

import filecmp
import os
import shutil
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/workspace")  # prepare and collect: the whole volume
WORK = Path("/work")  # run: the notebook's folder, read-only, with outputs/ writable
OUT = Path("/out")  # run: the run folder
RUNS = ".craft-runs"
STALE_SECONDS = 24 * 3600
CELL_SECONDS = 120
CREATED = ".created-outputs"  # prepare made the folder's outputs/, so collect may remove it
STATUS = ".status"


def _env() -> tuple[str, str, str, str]:
    return os.environ["RUN"], os.environ["FOLDER"], os.environ["NOTEBOOK"], os.environ["OUTPUT"]


def _files(top: Path):
    """Regular files under ``top``, relative to it; links and special files are skipped."""
    for folder, folders, names in os.walk(top):
        folders[:] = [name for name in folders if not (Path(folder) / name).is_symlink()]
        for name in names:
            path = Path(folder) / name
            if path.is_file() and not path.is_symlink():
                yield path.relative_to(top)


def prepare(root: Path = ROOT) -> None:
    run, folder, _, _ = _env()
    runs = root / RUNS
    runs.mkdir(exist_ok=True)
    for old in runs.iterdir():  # left behind by runs that failed before collect
        if old.is_dir() and not old.is_symlink():
            if time.time() - old.stat().st_mtime > STALE_SECONDS:
                shutil.rmtree(old, ignore_errors=True)
    here = runs / run
    (here / "outputs").mkdir(parents=True)
    outputs = root / folder / "outputs"
    if outputs.is_dir() and not outputs.is_symlink():
        # The run sees the outputs it had, as it did in a copied folder; collect skips the
        # ones it leaves unchanged.
        for relative in _files(outputs):
            (here / "outputs" / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(outputs / relative, here / "outputs" / relative)
    else:
        # The run's outputs/ is mounted over this path, so it must exist on the volume.
        outputs.mkdir()
        (here / CREATED).touch()


def run(work: Path = WORK, out: Path = OUT) -> None:
    # The workspace image's own packages, not the app's.
    import nbformat  # pyright: ignore[reportMissingImports]
    from nbclient import NotebookClient  # pyright: ignore[reportMissingImports]
    from nbclient.exceptions import CellExecutionError  # pyright: ignore[reportMissingImports]

    _, _, name, output = _env()
    notebook = nbformat.read(work / name, as_version=4)
    client = NotebookClient(
        notebook,
        timeout=CELL_SECONDS,
        kernel_name="python3",
        resources={"metadata": {"path": str(work)}},
    )
    status = 0
    try:
        client.execute()
    except CellExecutionError:
        status = 3
    nbformat.write(notebook, out / output)
    (out / STATUS).write_text(str(status))


def _free(destination: Path, stamp: str) -> Path:
    if not destination.exists() and not destination.is_symlink():
        return destination
    renamed = destination.with_name(f"{destination.stem}.run-{stamp}{destination.suffix}")
    if not renamed.exists() and not renamed.is_symlink():
        return renamed
    return destination.with_name(
        f"{destination.stem}.run-{stamp}-{uuid.uuid4().hex[:6]}{destination.suffix}"
    )


def _move(source: Path, destination: Path, stamp: str) -> Path:
    """Move without ever replacing a file: a hard link fails if the name is taken."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        target = _free(destination, stamp)
        try:
            os.link(source, target)
        except FileExistsError:
            continue
        source.unlink()
        return target
    raise FileExistsError(destination)


def collect(root: Path = ROOT) -> int:
    run, folder, _, output = _env()
    here = root / RUNS / run
    status = int((here / STATUS).read_text()) if (here / STATUS).is_file() else 1
    executed = here / output
    if not executed.is_file() or executed.is_symlink():
        print("the run left no executed copy", file=sys.stderr)
        return 1
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = root / folder
    _move(executed, target / output, stamp)
    for relative in sorted(_files(here / "outputs")):
        made, before = here / "outputs" / relative, target / "outputs" / relative
        if before.is_file() and filecmp.cmp(made, before, shallow=False):
            continue  # an output the notebook did not change
        _move(made, before, stamp)
    outputs = target / "outputs"
    if (here / CREATED).exists() and outputs.is_dir() and not any(outputs.iterdir()):
        outputs.rmdir()
    shutil.rmtree(here)
    return 0 if status in (0, 3) else 2


def main() -> None:
    step = sys.argv[1]
    if step == "prepare":
        prepare()
    elif step == "run":
        run()
    elif step == "collect":
        sys.exit(collect())
    else:
        sys.exit(f"unknown step {step}")


if __name__ == "__main__":
    main()
