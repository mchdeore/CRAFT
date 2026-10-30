"""Folder-backed originals, immutable versions and a numbered change feed."""

import hashlib
import json
import mimetypes
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.infrastructure.provider.locking import synchronized, synchronized_read

SILOS = {"requirements", "quality", "launch", "engineering", "reviews", "budget"}
STAGES = {
    "st1-concept",
    "st2-definition",
    "st3-design",
    "st4-build",
    "st5-verification",
    "st6-operation",
    "st7-closure",
}


def source_digest(file: Path) -> str:
    with file.open("rb") as source:
        digest = hashlib.sha256()
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def captured_source(file: Path, directory: Path) -> Iterator[tuple[Path, str]]:
    # Hash the same streamed bytes that become the immutable snapshot.
    path = directory / f".capture-{uuid4().hex}"
    try:
        with path.open("xb") as staged:
            digest = hashlib.sha256()
            with file.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    staged.write(chunk)
        yield path, digest.hexdigest()
    finally:
        path.unlink(missing_ok=True)


def tags(path: str) -> dict[str, Any]:
    """Mission, silo and stage from a path like missions/<mission>/<silo>/<stage>/..."""
    parts = Path(path).parts
    mission = (
        parts[1]
        if len(parts) >= 2 and parts[0] == "missions" and parts[1].startswith("msn-")
        else None
    )
    silo = parts[2] if mission and len(parts) > 2 and parts[2] in SILOS else None
    stage = parts[3] if silo and len(parts) > 3 and parts[3] in STAGES else None
    return {
        "mission_key": mission,
        "silo": silo,
        "stage": stage,
        "warnings": ["unknown_silo"] if mission and len(parts) > 3 and silo is None else [],
    }


def under(path: str, folders: list[str]) -> bool:
    return any(
        folder in {"", "."} or path.startswith(folder.rstrip("/") + "/") for folder in folders
    )


class SimulatedProvider:
    def __init__(self, root: str | Path, *, clock: str | None = None) -> None:
        self.root = Path(root).resolve()
        self.meta = self.root / ".provider"
        self.clock = clock

    def path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or any(
            p.startswith(".") for p in Path(relative).parts
        ):
            raise ValueError("invalid provider path")
        return path

    def items(self) -> dict[str, dict[str, Any]]:
        # save() replaces the file in one rename, so a read needs no lock and sees a whole state.
        file = self.meta / "items.jsonl"
        return (
            {r["item"]: r for line in file.read_text().splitlines() if (r := json.loads(line))}
            if file.exists()
            else {}
        )

    @synchronized
    def save(self, items: dict[str, dict[str, Any]]) -> None:
        self.meta.mkdir(parents=True, exist_ok=True)
        temp = self.meta / "items.tmp"
        temp.write_text(
            "".join(json.dumps(items[key], sort_keys=True) + "\n" for key in sorted(items))
        )
        temp.replace(self.meta / "items.jsonl")

    @synchronized
    def feed(self, item: str, kind: str) -> dict[str, Any]:
        self.meta.mkdir(parents=True, exist_ok=True)
        file = self.meta / "changes.jsonl"
        last = 0
        if file.exists():
            for line in file.read_text().splitlines():
                last = json.loads(line)["cursor"]
        record = {"cursor": last + 1, "item": item, "kind": kind}
        with file.open("a") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def tags(self, path: str) -> dict[str, Any]:
        return tags(path)

    @synchronized
    def scan(self) -> list[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True)
        items = self.items()
        active = {r["path"]: r for r in items.values() if r["deleted_at"] is None}
        files, unread = self.walk()
        # A folder that could not be read keeps its items: a partial scan is not a deletion.
        absent = {
            path: r for path, r in active.items() if path not in files and not under(path, unread)
        }
        changes = []
        now = self.clock or datetime.now(UTC).isoformat()
        for path, file in sorted(files.items()):
            existing = active.get(path)
            if (
                existing is not None
                and existing["versions"]
                and source_digest(file) == existing["versions"][-1]["sha256"]
            ):
                existing.update(self.tags(path))
                existing["title"] = file.stem.replace("-", " ").replace("_", " ")
                continue
            with captured_source(file, self.meta) as (staged, digest):
                record = active.get(path)
                kind = None
                if record is None:
                    moved = next(
                        (
                            old
                            for old, r in sorted(absent.items())
                            if r["versions"][-1]["sha256"] == digest
                        ),
                        None,
                    )
                    if moved is not None:
                        record = absent.pop(moved)
                        record["path"] = path
                        kind = "moved"
                    else:
                        key = "itm_" + hashlib.sha256(path.encode()).hexdigest()[:12]
                        while key in items:
                            key = "itm_" + hashlib.sha256((key + path).encode()).hexdigest()[:12]
                        record = {
                            "item": key,
                            "path": path,
                            "title": file.stem.replace("-", " ").replace("_", " "),
                            "simulated": True,
                            "classification": "unclassified",
                            "deleted_at": None,
                            "versions": [],
                        }
                        items[key] = record
                        kind = "added"
                record.update(self.tags(path))
                record["title"] = file.stem.replace("-", " ").replace("_", " ")
                if not record["versions"] or record["versions"][-1]["sha256"] != digest:
                    version = len(record["versions"]) + 1
                    snapshot = self.meta / "versions" / record["item"] / str(version)
                    snapshot.parent.mkdir(parents=True, exist_ok=True)
                    staged.replace(snapshot)
                    record["versions"].append(
                        {
                            "version": version,
                            "sha256": digest,
                            "file_name": file.name,
                            "media_type": mimetypes.guess_type(path)[0] or "text/plain",
                        }
                    )
                    kind = kind or "changed"
                if kind:
                    record["updated_at"] = now
                    changes.append(self.feed(record["item"], kind))
        for record in absent.values():
            record["deleted_at"] = now
            record["updated_at"] = now
            changes.append(self.feed(record["item"], "deleted"))
        self.save(items)
        return changes

    def walk(self) -> tuple[dict[str, Path], list[str]]:
        """Visible files by relative path, and the folders that could not be read."""
        files: dict[str, Path] = {}
        unread: list[str] = []

        def failed(error: OSError) -> None:
            unread.append(Path(error.filename).relative_to(self.root).as_posix())

        for folder, folders, names in os.walk(self.root, onerror=failed):
            base = Path(folder)
            folders[:] = [name for name in folders if not name.startswith(".")]
            relative = base.relative_to(self.root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            for name in names:
                file = base / name
                if not name.startswith(".") and file.is_file() and not file.is_symlink():
                    files[(relative / name).as_posix()] = file
        return files, unread

    @synchronized_read
    def list_changes(self, cursor: int) -> tuple[list[dict[str, Any]], int]:
        file = self.meta / "changes.jsonl"
        changes = (
            [
                r
                for line in file.read_text().splitlines()
                if (r := json.loads(line))["cursor"] > cursor
            ][:500]
            if file.exists()
            else []
        )
        return changes, changes[-1]["cursor"] if changes else cursor

    def get_item(self, item: str) -> dict[str, Any]:
        if item not in (items := self.items()):
            raise KeyError(item)
        return items[item]

    def read_content(self, item: str, version: int) -> bytes:
        # A listed version's snapshot is complete and never changes, so it is read without a lock.
        if version not in {v["version"] for v in self.get_item(item)["versions"]}:
            raise KeyError("unknown version")
        return (self.meta / "versions" / item / str(version)).read_bytes()

    @synchronized
    def write_content(self, path: str, content: bytes) -> None:
        file = self.path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        self.scan()

    @synchronized
    def delete_item(self, item: str) -> None:
        record = self.get_item(item)
        if record["deleted_at"] is not None:
            return
        self.path(record["path"]).unlink(missing_ok=True)
        self.scan()
