"""Agent actions in the owner's own workspace; each one waits for the requester's approval."""

from typing import Annotated, Any, ClassVar, Literal

import msgspec

from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import EXTERNAL, operation
from app.infrastructure.shapes import Shape
from app.workspace import service

NotebookPath = Annotated[str, msgspec.Meta(min_length=1, max_length=255, pattern=r"\.ipynb$")]


class RunCode(Shape):
    code: Annotated[str, msgspec.Meta(min_length=1, max_length=100_000)]
    # The notebook this code works on; approving it grants that notebook for the conversation.
    notebook: NotebookPath | None = None


class GrantedRun(Shape):
    notebook: NotebookPath
    code: Annotated[str, msgspec.Meta(min_length=1, max_length=100_000)]


class KernelImage(Shape):
    media_type: Literal["image/png", "image/jpeg"]
    data_base64: str


class CodeResult(Shape):
    status: Literal["ok", "error"]
    outputs: list[str]
    error: str | None
    truncated: bool
    traceback: list[str] = []
    images: list[KernelImage] = []


class WriteFile(Shape):
    path: Annotated[str, msgspec.Meta(min_length=1, max_length=255)]
    content: Annotated[str, msgspec.Meta(max_length=1_000_000)]


class EditNotebook(Shape):
    path: NotebookPath
    action: Literal["insert", "replace", "delete"]
    index: Annotated[int, msgspec.Meta(ge=0, le=10_000)]
    source: Annotated[str, msgspec.Meta(max_length=100_000)] = ""
    cell_type: Literal["code", "markdown"] = "code"


class SaveChart(Shape):
    max_bytes: ClassVar[int] = 1024 * 1024
    name: Annotated[str, msgspec.Meta(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
    spec: dict[str, Any]
    title: Annotated[str, msgspec.Meta(min_length=1, max_length=200)] | None = None


class SavedChart(Shape):
    svg: str
    spec: str
    csv: str


class RunNotebook(Shape):
    path: Annotated[str, msgspec.Meta(min_length=1, max_length=255, pattern=r"\.ipynb$")]


class NotebookRunQueued(Shape):
    job_id: str
    notebook: str
    output: str


class SavedFile(Shape):
    path: str
    size: int


class EditedNotebook(Shape):
    path: str
    cells: int


@operation(SIGNED_IN, post="/workspace/run", tool=True, effects=EXTERNAL, grants="notebook")
async def run_code(caller: Caller, request: RunCode) -> CodeResult:
    """Run Python in the requester's workspace kernel after approval; for example an analysis."""
    return CodeResult(**await service.run_code(caller, request.code))


@operation(
    SIGNED_IN, post="/workspace/notebooks/run", tool=True, effects=EXTERNAL, granted="notebook"
)
async def run_in_granted_notebook(caller: Caller, request: GrantedRun) -> CodeResult:
    """Run Python for a notebook already granted in this conversation, without asking again."""
    return CodeResult(**await service.run_code(caller, request.code))


@operation(SIGNED_IN, put="/workspace/files", tool=True, effects=EXTERNAL)
async def write_file(caller: Caller, request: WriteFile) -> SavedFile:
    """Create or replace one text file under USER_ROOT after approval; for example a script."""
    return SavedFile(**await service.write_file(caller, request.path, request.content))


@operation(SIGNED_IN, post="/workspace/notebooks/edit", tool=True, effects=EXTERNAL, grants="path")
async def edit_notebook(caller: Caller, request: EditNotebook) -> EditedNotebook:
    """Insert, replace or delete one notebook cell after approval; for example a new code cell."""
    return EditedNotebook(**await service.edit_notebook(caller, request))


@operation(SIGNED_IN, post="/workspace/charts", tool=True, effects=EXTERNAL)
async def save_chart(caller: Caller, request: SaveChart) -> SavedChart:
    """Save a chart as SVG, Vega-Lite spec and CSV under outputs/charts after approval."""
    return SavedChart(**await service.save_chart(caller, request))


@operation(
    SIGNED_IN, post="/workspace/notebooks/edit-granted", tool=True, effects=EXTERNAL, granted="path"
)
async def edit_granted_notebook(caller: Caller, request: EditNotebook) -> EditedNotebook:
    """Change a cell of a notebook already granted in this conversation, without asking again."""
    return EditedNotebook(**await service.edit_notebook(caller, request))


@operation(SIGNED_IN, post="/workspace/notebooks/runs", tool=True, effects=EXTERNAL)
async def run_notebook(caller: Caller, request: RunNotebook) -> NotebookRunQueued:
    """Run a whole notebook unattended in a sandbox after approval; the original is unchanged."""
    return NotebookRunQueued(**await service.run_notebook(caller, request.path))
