"""The notebook_run job: a whole notebook runs in a fresh sandbox, never in the owner's kernel."""

from typing import Annotated

import msgspec

from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.notebook_runs import JOB_SECONDS
from app.infrastructure.operations import Job, operation
from app.infrastructure.shapes import Shape
from app.workspace import service


class NotebookRunJob(Shape):
    path: Annotated[str, msgspec.Meta(min_length=1, max_length=255, pattern=r"\.ipynb$")]
    output: Annotated[str, msgspec.Meta(min_length=1, max_length=255, pattern=r"\.ipynb$")]


@operation(SIGNED_IN, job=Job(queue="interactive", timeout=JOB_SECONDS, notify=True))
async def notebook_run(caller: Caller, request: NotebookRunJob) -> None:
    """Run one owned notebook in a sandbox and copy its executed copy and outputs back."""
    await service.notebook_job(caller, request.path, request.output)
