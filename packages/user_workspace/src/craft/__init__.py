"""Notebook-friendly access to an owned root and the existing CRAFT MCP tools."""

import os
from pathlib import Path

from craft.client import Craft, connect, native

USER_ROOT = Path(os.environ.get("USER_ROOT", "/workspace"))
if not USER_ROOT.is_absolute():
    raise ValueError("USER_ROOT must be an absolute owned mount")


def owned_path(relative: str, *, root: Path = USER_ROOT) -> Path:
    """Resolve a relative owned path, rejecting traversal and every existing symlink.

    This is a path convenience, not a Python sandbox. The container mount and
    operating-system boundary must confine kernels and terminals.
    """
    if (
        not relative
        or "\\" in relative
        or any(ord(value) < 32 for value in relative)
        or Path(relative).is_absolute()
        or any(value in {"", ".", ".."} for value in relative.split("/"))
    ):
        raise ValueError("Expected a relative owned path")
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise ValueError("Expected an existing owned root without symlinks")
    path = root
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError("Symlinks are outside the owned path boundary")
    return path


__all__ = ["USER_ROOT", "Craft", "connect", "native", "owned_path"]
