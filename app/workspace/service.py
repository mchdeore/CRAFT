"""Reach only the caller's own configured workspace; no endpoint comes from a request."""

from typing import Any
from uuid import uuid4

from app.infrastructure import workspaces
from app.infrastructure.errors import InvalidInput, NotFound


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
