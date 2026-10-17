"""Atomic Markdown references rendered from extraction blocks."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from .models import BlockInput


def render_reference(
    *, name: str, metadata: dict[str, object], blocks: Sequence[BlockInput]
) -> str:
    safe_metadata = json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).replace("-->", "--\\u003e")
    title = name.replace("\r", " ").replace("\n", " ").strip() or "Untitled"
    lines = [f"# {title}", "", f"<!-- craft-reference-v1 {safe_metadata} -->", ""]
    for index, block in enumerate(blocks, start=1):
        if block.heading:
            lines.extend([f"## {block.heading}", ""])
        lines.extend(
            [
                f"<!-- block {index}; "
                f"location={json.dumps(block.location, ensure_ascii=False)} -->",
                block.text,
                "",
            ]
        )
    return "\n".join(lines)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink(missing_ok=True)
        finally:
            raise


ReferenceRenderer = Callable[..., str]
