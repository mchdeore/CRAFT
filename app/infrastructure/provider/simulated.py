"""Folder-backed originals, immutable versions and a numbered change feed."""

import hashlib
import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
        file = self.meta / "items.jsonl"
        return (
            {r["item"]: r for line in file.read_text().splitlines() if (r := json.loads(line))}
            if file.exists()
            else {}
        )

    def save(self, items: dict[str, dict[str, Any]]) -> None:
        self.meta.mkdir(parents=True, exist_ok=True)
        temp = self.meta / "items.tmp"
        temp.write_text(
            "".join(json.dumps(items[key], sort_keys=True) + "\n" for key in sorted(items))
        )
        temp.replace(self.meta / "items.jsonl")

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

    def scan(self) -> list[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True)
        items = self.items()
        active = {r["path"]: r for r in items.values() if r["deleted_at"] is None}
        files = {
            p.relative_to(self.root).as_posix(): p
            for p in self.root.rglob("*")
            if p.is_file()
            and not p.is_symlink()
            and not any(part.startswith(".") for part in p.relative_to(self.root).parts)
        }
        absent = {path: r for path, r in active.items() if path not in files}
        changes = []
        now = self.clock or datetime.now(UTC).isoformat()
        for path, file in sorted(files.items()):
            content = file.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
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
                snapshot.write_bytes(content)
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
        record = self.get_item(item)
        if version not in {v["version"] for v in record["versions"]}:
            raise KeyError("unknown version")
        return (self.meta / "versions" / item / str(version)).read_bytes()

    def write_content(self, path: str, content: bytes) -> None:
        file = self.path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        self.scan()

    def delete_item(self, item: str) -> None:
        record = self.get_item(item)
        if record["deleted_at"] is not None:
            return
        self.path(record["path"]).unlink(missing_ok=True)
        self.scan()
