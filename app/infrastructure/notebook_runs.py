"""The shape every notebook-run backend has, and the limits they share.

A backend runs one owned notebook unattended, offline and bounded, then leaves the executed
copy and any new or changed files in its ``outputs/`` folder beside the notebook, never
overwriting a file. The workspace service picks a backend and codes only against
``NotebookRunner``:

- ``notebook_jobs.JobRunner``: a Kubernetes Job on the owner's volume, on the local cluster.
- ``notebook_sandbox.SandboxRunner``: a Docker container under Compose, until K-remove-docker.

The runner inside the workspace image (``workspace/src/craft_run``) keeps its own copy of the
per-cell limit, because that package can't import the app.
"""

from typing import Protocol

TOTAL_SECONDS = 15 * 60  # one run, start to finish
CELL_SECONDS = 120  # one cell


class RunError(RuntimeError):
    """A run that didn't finish. The message is one fixed code, never notebook content:
    ``run_failed``, ``run_timeout``, ``cancelled``, ``copy_failed`` or ``folder_too_large``."""


class NotebookRunner(Protocol):
    """Runs one notebook of one owner; cancelling ``run`` stops the run at once."""

    async def run(self, folder: str, notebook: str, output: str) -> None:
        """Execute ``folder/notebook`` and save the executed copy as ``folder/output``.

        ``folder`` is relative to the owner's workspace root ("" for the root itself). Raises
        ``RunError`` when the run fails or times out; a notebook whose cell raised still counts
        as finished, with the error in its executed copy.
        """
        ...
