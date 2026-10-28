"""Reach only the caller's own configured workspace; no endpoint comes from a request."""

import asyncio
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from app.infrastructure import workspaces
from app.infrastructure.errors import Conflict, InvalidInput, NotFound


def _target(caller) -> workspaces.JupyterTarget:
    entry = workspaces.owner_entry(caller.user_id)
    if entry is None or "jupyter" not in entry:
        raise NotFound()
    return workspaces.jupyter_target(entry)


def _name(path: str) -> str:
    try:
        return workspaces.owned_name(path)
    except ValueError:
        raise InvalidInput() from None


async def run_code(caller, code: str) -> dict[str, Any]:
    return await workspaces.run_in_kernel(_target(caller), code)


async def write_file(caller, path: str, content: str) -> dict[str, Any]:
    name = _name(path)
    await workspaces.write_text(_target(caller), name, content)
    return {"path": name, "size": len(content.encode())}


async def save_chart(caller, request) -> dict[str, Any]:
    from starlette.concurrency import run_in_threadpool

    from app.tools.handlers import workspace_chart

    made = await run_in_threadpool(workspace_chart, request.spec, request.title)
    target = _target(caller)
    saved = {}
    for suffix, content in made.items():
        path = f"outputs/charts/{request.name}.{suffix}"
        await workspaces.write_text(target, path, content)
        saved[suffix.removeprefix("vl.").replace("json", "spec")] = path
    return saved


async def edit_notebook(caller, request) -> dict[str, Any]:
    name = _name(request.path)
    target = _target(caller)
    try:
        notebook = await workspaces.read_notebook(target, name)
    except Exception:
        if request.action != "insert" or request.index != 0:
            raise NotFound() from None
        notebook = {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    cells = notebook.setdefault("cells", [])
    limit = len(cells) if request.action == "insert" else len(cells) - 1
    if request.index > limit:
        raise InvalidInput()
    cell: dict[str, Any] = {
        "id": uuid4().hex[:8],
        "cell_type": request.cell_type,
        "metadata": {},
        "source": request.source,
    }
    if request.cell_type == "code":
        cell.update(execution_count=None, outputs=[])
    if request.action == "insert":
        cells.insert(request.index, cell)
    elif request.action == "replace":
        # The cell keeps its identity; a new source clears earlier outputs.
        cells[request.index] = {**cell, "id": cells[request.index].get("id", cell["id"])}
    else:
        del cells[request.index]
    await workspaces.write_notebook(target, name, notebook)
    return {"path": name, "cells": len(cells)}


NOTEBOOK_RUNS = 2  # queued or running, per requester


async def run_notebook(caller, path: str) -> dict[str, Any]:
    from app.infrastructure.database.unit_of_work import unit_of_work
    from app.infrastructure.operations.jobs import enqueue
    from app.jobs.handlers import running_jobs
    from app.workspace.jobs import NotebookRunJob, notebook_run

    name = _name(path)
    target = _target(caller)
    try:
        await workspaces.read_notebook(target, name)
    except Exception:
        raise NotFound() from None
    notebook = PurePosixPath(name)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    output = f"{notebook.stem}.run-{stamp}.ipynb"
    assert caller.sessions is not None
    with unit_of_work(caller.sessions) as session:
        if running_jobs(session, notebook_run.__operation__.name, caller.user_id) >= NOTEBOOK_RUNS:
            raise Conflict()
        job = enqueue(session, caller, notebook_run, NotebookRunJob(path=name, output=output))
    folder = str(notebook.parent) if str(notebook.parent) != "." else ""
    return {"job_id": str(job), "notebook": name, "output": f"{folder}/{output}".lstrip("/")}


def _free(name: str, taken: set[str], stamp: str) -> str:
    """The name itself, or name.run-<stamp>.ext beside it when that file already exists."""
    if name not in taken:
        return name
    path = PurePosixPath(name)
    return str(path.with_name(f"{path.stem}.run-{stamp}{path.suffix}"))


async def notebook_job(caller, path: str, output: str) -> None:
    from app.infrastructure.notebook_sandbox import FOLDER_LIMIT, Sandbox

    name = _name(path)
    target = _target(caller)
    notebook = PurePosixPath(name)
    folder = "" if str(notebook.parent) == "." else str(notebook.parent)
    files = await workspaces.read_folder(target, folder, FOLDER_LIMIT)
    sandbox = Sandbox(caller.settings.notebook_image)
    try:
        code, made = await asyncio.to_thread(sandbox.run, files, notebook.name, output)
    finally:
        # A cancelled job lands here first: removing the container stops the run at once.
        sandbox.kill()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    before = {f"{folder}/{key}".lstrip("/") for key in files}
    for relative, content in sorted(made.items()):
        if relative.split("/", 1)[0] == "outputs" and files.get(relative) == content:
            continue  # an output the notebook did not change
        destination = _free(f"{folder}/{relative}".lstrip("/"), before, stamp)
        # Never overwrite: an existing file keeps its content and the run's copy is renamed.
        if await workspaces.exists(target, destination):
            destination = _free(destination, {destination}, stamp + "-" + uuid4().hex[:6])
        await workspaces.write_bytes(target, _name(destination), content)
    if code not in (0, 3):
        raise RuntimeError("The notebook run failed")
