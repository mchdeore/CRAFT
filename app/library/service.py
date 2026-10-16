"""Resource checks precede retrieval; services select short read transactions."""

import math
from collections import Counter

from starlette.responses import Response

from app.infrastructure.access import Caller
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import InvalidInput, NotFound
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library import storage
from app.library.embed import embed, model_name


def inspect_file(request):
    import hashlib
    from pathlib import Path

    from app.library.convert import convert
    from app.library.handlers import InspectedBlock, InspectedFile

    suffix = Path(request.file_name).suffix.lower()
    types = {
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".html": "text/html",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv",
    }
    if (
        not request.content
        or Path(request.file_name).name != request.file_name
        or "\\" in request.file_name
        or "\x00" in request.file_name
        or types.get(suffix) != request.media_type
    ):
        raise InvalidInput()
    try:
        blocks = convert(request.content, request.file_name)
    except (ValueError, UnicodeDecodeError):
        raise InvalidInput() from None
    if not blocks:
        raise InvalidInput()
    return InspectedFile(
        file_name=request.file_name,
        media_type=request.media_type,
        sha256=hashlib.sha256(request.content).hexdigest(),
        simulated=request.simulated,
        blocks=[
            InspectedBlock(
                block=block.block,
                kind=block.kind,
                heading_path=block.heading_path,
                locator=block.locator,
                text=block.text,
                quote_sha256=hashlib.sha256(block.text.encode()).hexdigest(),
            )
            for block in blocks
        ],
    )


def scope(caller: Caller):
    if caller.sessions is None or caller.settings is None:
        raise NotFound()
    return caller.sessions, caller.settings


def handle(item, version: int, block):
    from app.library.handlers import CitationHandle

    return CitationHandle(
        item=item["item"],
        version=version,
        block=block["block"],
        locator=block["locator"],
        quote_sha256=bytes(block["text_sha256"]).hex(),
        simulated=item["simulated"],
    )


def chunk_text(item, version: int, block, score=None):
    from app.library.handlers import ChunkText

    return ChunkText(
        handle=handle(item, version, block),
        heading_path=block["heading_path"],
        text=block["text"],
        score=score,
    )


def tags(item):
    from app.library.handlers import Tags

    return Tags(mission=item["mission_key"], silo=item["silo"], stage=item["stage"])


def search(caller: Caller, request):
    from app.library.handlers import LibraryHit, LibraryHits

    sessions, settings = scope(caller)
    vector = embed(request.query, caller=caller)
    with unit_of_work(sessions, read_only=True) as session:
        candidates = storage.candidates(session, caller, request, vector, model_name(settings))
    maxima = {
        method: max(
            (
                max(0, r["raw_score"])
                for _, m, r in candidates
                if m == method and math.isfinite(r["raw_score"])
            ),
            default=0,
        )
        for method in ["lexical", "semantic"]
    }
    combined = {}
    for kind, method, row in candidates:
        key = (kind, row["item"], row["version"], row.get("block", 0))
        entry = combined.setdefault(key, {"row": row, "kind": kind, "score": 0.0})
        score = row["raw_score"]
        if maxima[method] and math.isfinite(score):
            entry["score"] += (
                max(0, score) / maxima[method] * (0.7 if method == "semantic" else 0.3)
            )
    ranked = sorted(
        combined.values(), key=lambda r: (-r["score"], r["row"]["item"], r["row"].get("block", 0))
    )
    spread = Counter()
    hits = []
    provider = SimulatedProvider(settings.provider_root)
    latest_versions = {}
    for entry in ranked:
        key = entry["row"]["item"]
        if key not in latest_versions:
            try:
                latest_versions[key] = provider.get_item(key)["versions"][-1]["version"]
            except KeyError:
                latest_versions[key] = None
    with unit_of_work(sessions, read_only=True) as session:
        for entry in ranked:
            row = entry["row"]
            kind = entry["kind"]
            if entry["score"] <= 0:
                continue
            if row["version"] != latest_versions[row["item"]]:
                continue
            section = (row["version_id"], row.get("heading_path", ""))
            if kind == "chunk" and spread[section] >= 2:
                continue
            if kind == "chunk":
                block = row
                neighbours = storage.blocks(
                    session, row["version_id"], max(1, row["block"] - 1), row["block"] + 1
                )
                before = next((b for b in neighbours if b["block"] == row["block"] - 1), None)
                after = next((b for b in neighbours if b["block"] == row["block"] + 1), None)
                spread[section] += 1
            else:
                blocks = storage.blocks(session, row["version_id"], 1, 1)
                if not blocks:
                    continue
                block = blocks[0]
                before = after = None
            hits.append(
                LibraryHit(
                    kind=kind,
                    handle=handle(row, row["version"], block),
                    title=row["title"],
                    heading_path=block["heading_path"] if kind == "chunk" else "",
                    text=row["text"],
                    score=entry["score"],
                    tags=tags(row),
                    classification=row["classification"],
                    before=chunk_text(row, row["version"], before) if before else None,
                    after=chunk_text(row, row["version"], after) if after else None,
                )
            )
            if len(hits) > request.limit:
                break
    return LibraryHits(hits=hits[: request.limit], truncated=len(hits) > request.limit)


def readable_version(session, caller: Caller, key: str, version: int | None):
    item = storage.item(session, caller, key)
    if item is None:
        raise NotFound()
    versions = storage.versions(session, key)
    selected = (
        next((v for v in versions if v["version"] == version), None)
        if version is not None
        else versions[-1]
        if versions
        else None
    )
    if selected is None:
        raise NotFound()
    return item, selected, versions


def document(caller: Caller, request):
    from app.library.handlers import LibraryDocument, Outline, Version

    sessions, _ = scope(caller)
    with unit_of_work(sessions, read_only=True) as session:
        item, version, versions = readable_version(session, caller, request.item, request.version)
        blocks = storage.blocks(session, version["id"])
    return LibraryDocument(
        item=item["item"],
        version=version["version"],
        title=item["title"],
        path=item["path"],
        tags=tags(item),
        summary=version["summary"],
        summary_kind=version["summary_kind"],
        outline=[Outline(block=b["block"], heading_path=b["heading_path"]) for b in blocks],
        versions=[
            Version(
                version=v["version"],
                sha256=bytes(v["sha256"]).hex(),
                indexed_at=v["indexed_at"].isoformat(),
            )
            for v in versions
        ],
        simulated=item["simulated"],
        classification=item["classification"],
        deleted=item["deleted_at"] is not None,
    )


def read_chunks(caller: Caller, request):
    from app.library.handlers import ChunkPage

    if (
        request.from_block < 1
        or request.to_block < request.from_block
        or request.to_block - request.from_block >= 20
    ):
        raise InvalidInput()
    sessions, _ = scope(caller)
    with unit_of_work(sessions, read_only=True) as session:
        item, version, _ = readable_version(session, caller, request.item, request.version)
        blocks = storage.blocks(session, version["id"], request.from_block, request.to_block)
    return ChunkPage(chunks=[chunk_text(item, request.version, b) for b in blocks], truncated=False)


def explore(caller: Caller, request):
    from app.library.handlers import ChunkPage

    sessions, _ = scope(caller)
    with unit_of_work(sessions, read_only=True) as session:
        item, version, _ = readable_version(
            session, caller, request.handle.item, request.handle.version
        )
        block = storage.blocks(session, version["id"], request.handle.block, request.handle.block)
        if (
            not block
            or block[0]["locator"] != request.handle.locator
            or bytes(block[0]["text_sha256"]).hex() != request.handle.quote_sha256
        ):
            raise NotFound()
        blocks = storage.blocks(
            session,
            version["id"],
            max(1, request.handle.block - request.before),
            request.handle.block + request.after,
        )
    query = embed(request.query, caller=caller) if request.query else None
    return ChunkPage(
        chunks=[
            chunk_text(
                item,
                request.handle.version,
                b,
                sum(x * y for x, y in zip(query, embed(b["text"], caller=caller), strict=True))
                if query
                else None,
            )
            for b in blocks
        ],
        truncated=False,
    )


def compare_versions(caller: Caller, request):
    from app.library.diff import compare
    from app.library.handlers import DiffBlock, VersionDiff

    sessions, _ = scope(caller)
    with unit_of_work(sessions, read_only=True) as session:
        _, before, _ = readable_version(session, caller, request.item, request.from_version)
        _, after, _ = readable_version(session, caller, request.item, request.to_version)
        stored = (
            storage.differences(session, before["id"], after["id"])
            if request.to_version == request.from_version + 1
            else []
        )
        if stored:
            rows = [dict(row) for row in stored]
        else:
            old = {
                row["block"]: bytes(row["text_sha256"])
                for row in storage.blocks(session, before["id"])
            }
            new = {
                row["block"]: bytes(row["text_sha256"])
                for row in storage.blocks(session, after["id"])
            }
            rows = compare(old, new)
    return VersionDiff(blocks=[DiffBlock(**row) for row in rows[:500]], truncated=len(rows) > 500)


def check_citation(caller: Caller, request):
    import hashlib

    from app.library.citation import quote
    from app.library.convert import convert
    from app.library.handlers import CitationStatus

    handle = request.handle
    if handle.version < 1 or handle.block < 1:
        raise InvalidInput()
    sessions, settings = scope(caller)
    with unit_of_work(sessions, read_only=True) as session:
        item, version, _ = readable_version(session, caller, handle.item, handle.version)
    provider = SimulatedProvider(settings.provider_root)
    try:
        live = provider.get_item(handle.item)
    except KeyError:
        raise NotFound() from None
    with unit_of_work(sessions, read_only=True) as session:
        if not storage.provider_readable(session, caller, live):
            raise NotFound()
    latest = max(row["version"] for row in live["versions"])
    if live["deleted_at"] is not None:
        return CitationStatus(status="withdrawn", latest_version=latest)
    try:
        source = provider.read_content(handle.item, handle.version)
    except KeyError:
        raise NotFound() from None
    original_version = next(row for row in live["versions"] if row["version"] == handle.version)
    filename = original_version.get("file_name", item["path"])
    try:
        blocks = convert(source, filename)
    except (ValueError, UnicodeDecodeError):
        return CitationStatus(status="changed", latest_version=latest)
    selected = next((block for block in blocks if block.block == handle.block), None)
    actual = quote(selected.text, handle.locator, selected.locator) if selected else None
    correct = (
        actual is not None and hashlib.sha256(actual.encode()).hexdigest() == handle.quote_sha256
    )
    return CitationStatus(
        status="changed"
        if not correct
        else "newer_version"
        if latest > handle.version
        else "current",
        latest_version=latest,
    )


def original(caller: Caller, request, attachment: bool) -> Response:
    sessions, settings = scope(caller)
    with unit_of_work(sessions, read_only=True) as session:
        item, version, _ = readable_version(session, caller, request.item, request.version)
    try:
        content = SimulatedProvider(settings.provider_root).read_content(
            request.item, request.version
        )
    except KeyError:
        raise NotFound() from None
    from pathlib import Path

    name = Path(item["path"]).name.replace('"', "").replace("\r", "").replace("\n", "")
    media_type = version["media_type"]
    inline = (
        media_type == "application/pdf"
        or media_type == "text/plain"
        or media_type.startswith("image/")
    )
    disposition = "attachment" if attachment or not inline else "inline"
    return Response(
        content,
        media_type=version["media_type"],
        headers={
            "Content-Disposition": f'{disposition}; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )
