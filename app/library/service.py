"""Resource checks precede retrieval; services select short read transactions."""

import math
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from starlette.responses import Response

from app.infrastructure.access import Caller
from app.infrastructure.database.unit_of_work import has_ambient_transaction, unit_of_work
from app.infrastructure.errors import Conflict, InvalidInput, NotFound
from app.infrastructure.operations import observe
from app.infrastructure.operations.context import current_call
from app.infrastructure.provider import source_provider as configured_provider
from app.library import storage
from app.library.embed import embed, model_name

if TYPE_CHECKING:
    from app.library.handlers import ChunkText


@dataclass(frozen=True, slots=True, repr=False)
class PreparedSource:
    """Server-owned source evidence sampled before an approved transaction."""

    provider_root: str
    item: str
    version: int
    version_id: UUID
    version_sha256: bytes
    block: int
    block_id: UUID
    locator: str
    quote_sha256: bytes
    path: str
    title: str
    mission: str | None
    silo: str | None
    stage: str | None
    classification: str
    simulated: bool
    source_mission: str | None
    source_silo: str | None
    block_locator: str
    block_sha256: bytes
    heading_path: str
    text: str

    def chunk(self) -> "ChunkText":
        from app.library.handlers import ChunkText, CitationHandle

        return ChunkText(
            handle=CitationHandle(
                item=self.item,
                version=self.version,
                block=self.block,
                locator=self.block_locator,
                quote_sha256=self.block_sha256.hex(),
                simulated=self.simulated,
            ),
            heading_path=self.heading_path,
            text=self.text,
        )


@observe("library.convert")
def _convert_observed(content: bytes, filename: str):
    """Timed convert step when an operation is already open."""
    from app.library.convert import convert

    return convert(content, filename)


def convert_request(content: bytes, filename: str):
    """Convert request bytes; emit a step only under an active operation."""
    from app.library.convert import convert

    if current_call.get() is None:
        return convert(content, filename)
    return _convert_observed(content, filename)


def inspect_file(request):
    import hashlib
    from pathlib import Path

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
        blocks = convert_request(request.content, request.file_name)
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
    provider = source_provider(settings)
    live = source_items(provider)
    vector = embed(request.query, caller=caller)
    with unit_of_work(sessions, read_only=True) as session:
        access = {}
        permitted = []
        for key, record in live.items():
            scope_key = (record["mission_key"], record["silo"])
            if scope_key not in access:
                access[scope_key] = storage.provider_readable(session, caller, record)
            if record["deleted_at"] is None and access[scope_key]:
                permitted.append(key)
        candidates = storage.candidates(
            session, caller, request, vector, model_name(settings), items=permitted
        )
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
    latest_versions = {}
    for entry in ranked:
        key = entry["row"]["item"]
        if key not in latest_versions:
            try:
                latest_versions[key] = live[key]["versions"][-1]["version"]
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


def source_item(provider, key: str):
    if has_ambient_transaction():
        raise NotFound()
    try:
        return provider.get_item(key)
    except (KeyError, FileNotFoundError):
        raise NotFound() from None


def source_provider(settings):
    if has_ambient_transaction():
        raise NotFound()
    return configured_provider(settings)


def source_items(provider):
    if has_ambient_transaction():
        raise NotFound()
    try:
        return provider.items()
    except (KeyError, FileNotFoundError):
        raise NotFound() from None


def _read_provider(provider, key: str, version: int):
    """Read provider bytes; refuse when a database transaction is open."""
    if has_ambient_transaction():
        raise NotFound()
    try:
        return provider.read_content(key, version)
    except (KeyError, FileNotFoundError):
        raise NotFound() from None


@observe("library.provider_read")
def _source_content_observed(provider, key: str, version: int):
    """Timed provider read when an operation is already open."""
    return _read_provider(provider, key, version)


def source_content(provider, key: str, version: int):
    """Read provider bytes; emit a step only under an active operation."""
    if current_call.get() is None:
        return _read_provider(provider, key, version)
    return _source_content_observed(provider, key, version)


def readable_source_version(session, caller: Caller, key: str, version: int | None, source):
    item, selected, versions = readable_version(session, caller, key, version)
    original = next(
        (row for row in source["versions"] if row["version"] == selected["version"]), None
    )
    if (
        item["deleted_at"] is not None
        or source["deleted_at"] is not None
        or not storage.provider_readable(session, caller, source)
        or original is None
        or original.get("sha256") != bytes(selected["sha256"]).hex()
    ):
        raise NotFound()
    return item, selected, versions


def documents(caller: Caller, request):
    from app.library.handlers import CatalogDocument, DocumentCatalog

    sessions, settings = scope(caller)
    provider = source_provider(settings)
    with unit_of_work(sessions, read_only=True) as session:
        candidates = storage.catalog_items(session, caller)
    rows = []
    for key in candidates[:200]:
        try:
            live = source_item(provider, key)
            with unit_of_work(sessions, read_only=True) as session:
                _, selected, _ = readable_source_version(session, caller, key, None, live)
                if max(row["version"] for row in live["versions"]) != selected["version"]:
                    continue
                version = selected["version"]
        except NotFound:
            continue
        title = live.get("title")
        classification = live.get("classification")
        simulated = live.get("simulated")
        if (
            not isinstance(title, str)
            or classification not in {"unclassified", "protected-a", "protected-b"}
            or not isinstance(simulated, bool)
        ):
            continue
        title = "".join(char for char in title if not unicodedata.category(char).startswith("C"))
        rows.append(
            CatalogDocument(
                item=key,
                title=title.strip()[:200] or "Untitled document",
                version=version,
                classification=classification,
                simulated=simulated,
            )
        )
        if len(rows) > request.limit:
            break
    return DocumentCatalog(
        documents=rows[: request.limit],
        truncated=len(candidates) > 200 or len(rows) > request.limit,
    )


def document(caller: Caller, request):
    from app.library.handlers import LibraryDocument, Outline, Version

    sessions, settings = scope(caller)
    live = source_item(source_provider(settings), request.item)
    with unit_of_work(sessions, read_only=True) as session:
        item, version, versions = readable_source_version(
            session, caller, request.item, request.version, live
        )
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
    sessions, settings = scope(caller)
    live = source_item(source_provider(settings), request.item)
    with unit_of_work(sessions, read_only=True) as session:
        item, version, _ = readable_source_version(
            session, caller, request.item, request.version, live
        )
        blocks = storage.blocks(session, version["id"], request.from_block, request.to_block)
    return ChunkPage(chunks=[chunk_text(item, request.version, b) for b in blocks], truncated=False)


def explore(caller: Caller, request):
    from app.library.handlers import ChunkPage

    sessions, settings = scope(caller)
    live = source_item(source_provider(settings), request.handle.item)
    with unit_of_work(sessions, read_only=True) as session:
        item, version, _ = readable_source_version(
            session, caller, request.handle.item, request.handle.version, live
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
        stored = (
            storage.chunk_vectors(session, [b["id"] for b in blocks], model_name(settings))
            if request.query
            else {}
        )
    # Stored chunk vectors score the neighbours; only the query is embedded here.
    query = embed(request.query, caller=caller) if request.query else None

    def score(block):
        vector = stored.get(block["id"])
        if query is None or vector is None:
            return None
        return sum(x * y for x, y in zip(query, vector, strict=True))

    return ChunkPage(
        chunks=[chunk_text(item, request.handle.version, b, score(b)) for b in blocks],
        truncated=False,
    )


def read_facets(caller: Caller, request):
    from app.library.handlers import FacetEvidence

    sessions, settings = scope(caller)
    with unit_of_work(sessions, read_only=True) as session:
        rows = storage.facet_records(session, request.missions, request.facets)
        source_ids = {item for row in rows for item in row["sources"]}
        cached = storage.provider_records(session, source_ids)
        cached_access = {
            item: storage.provider_readable(session, caller, row) for item, row in cached.items()
        }
    live = source_items(source_provider(settings))
    with unit_of_work(sessions, read_only=True) as session:
        live_access = {
            item: cached_access.get(item, False)
            and item in live
            and live[item]["deleted_at"] is None
            and storage.provider_readable(session, caller, live[item])
            for item in source_ids
        }
        visible = [
            row
            for row in rows
            if row["sources"]
            and all(
                live_access.get(item, False) and live[item]["mission_key"] == row["mission_key"]
                for item in row["sources"]
            )
        ]
        vectors = storage.facet_vectors(
            session, [row["id"] for row in visible], model_name(settings)
        )
    return [
        FacetEvidence(
            mission=row["mission_key"],
            facet=row["facet"],
            text=row["text"],
            sources=row["sources"],
            vector=vectors.get(row["id"]),
        )
        for row in visible
    ]


def compare_versions(caller: Caller, request):
    from app.library.diff import compare
    from app.library.handlers import DiffBlock, VersionDiff

    sessions, settings = scope(caller)
    live = source_item(source_provider(settings), request.item)
    with unit_of_work(sessions, read_only=True) as session:
        _, before, _ = readable_source_version(
            session, caller, request.item, request.from_version, live
        )
        _, after, _ = readable_source_version(
            session, caller, request.item, request.to_version, live
        )
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
    return checked_citation(caller, request.handle)[0]


def checked_citation(caller: Caller, handle):
    import hashlib

    from app.library.citation import quote
    from app.library.handlers import CitationStatus

    if handle.version < 1 or handle.block < 1:
        raise InvalidInput()
    sessions, settings = scope(caller)
    with unit_of_work(sessions, read_only=True) as session:
        item, version, _ = readable_version(session, caller, handle.item, handle.version)
    provider = source_provider(settings)
    live = source_item(provider, handle.item)
    with unit_of_work(sessions, read_only=True) as session:
        if not storage.provider_readable(session, caller, live):
            raise NotFound()
    if not live["versions"]:
        raise NotFound()
    latest = max(row["version"] for row in live["versions"])
    if live["deleted_at"] is not None:
        return CitationStatus(status="withdrawn", latest_version=latest), live, None
    original_version = next(
        (row for row in live["versions"] if row["version"] == handle.version), None
    )
    if original_version is None:
        raise NotFound()
    source = source_content(provider, handle.item, handle.version)
    source_sha256 = hashlib.sha256(source).digest()
    filename = original_version.get("file_name", item["path"])
    try:
        blocks = convert_request(source, filename)
    except (ValueError, UnicodeDecodeError):
        return CitationStatus(status="changed", latest_version=latest), live, source_sha256
    selected = next((block for block in blocks if block.block == handle.block), None)
    actual = quote(selected.text, handle.locator, selected.locator) if selected else None
    correct = (
        actual is not None and hashlib.sha256(actual.encode()).hexdigest() == handle.quote_sha256
    )
    return (
        CitationStatus(
            status="changed"
            if not correct
            else "newer_version"
            if latest > handle.version
            else "current",
            latest_version=latest,
        ),
        live,
        source_sha256,
    )


def prepare_source(caller: Caller, handle) -> PreparedSource:
    import hashlib

    from app.library.citation import quote

    status, live, actual_sha256 = checked_citation(caller, handle)
    if status.status != "current":
        raise Conflict()
    sessions, settings = scope(caller)
    with unit_of_work(sessions, read_only=True) as session:
        item, version, versions = readable_source_version(
            session, caller, handle.item, handle.version, live
        )
        if (
            versions[-1]["version"] != handle.version
            or actual_sha256 != bytes(version["sha256"])
            or handle.simulated != item["simulated"]
        ):
            raise Conflict()
        rows = storage.blocks(session, version["id"], handle.block, handle.block)
        if not rows:
            raise Conflict()
        block = rows[0]
        actual = quote(block["text"], handle.locator, block["locator"])
        if actual is None or hashlib.sha256(actual.encode()).hexdigest() != handle.quote_sha256:
            raise Conflict()
        return PreparedSource(
            provider_root=settings.provider_root,
            item=item["item"],
            version=version["version"],
            version_id=version["id"],
            version_sha256=bytes(version["sha256"]),
            block=block["block"],
            block_id=block["id"],
            locator=handle.locator,
            quote_sha256=bytes.fromhex(handle.quote_sha256),
            path=item["path"],
            title=item["title"],
            mission=item["mission_key"],
            silo=item["silo"],
            stage=item["stage"],
            classification=item["classification"],
            simulated=item["simulated"],
            source_mission=live["mission_key"],
            source_silo=live["silo"],
            block_locator=block["locator"],
            block_sha256=bytes(block["text_sha256"]),
            heading_path=block["heading_path"],
            text=block["text"],
        )


def validate_prepared_source(session, caller: Caller, prepared: PreparedSource) -> None:
    import hashlib

    from app.library.citation import quote

    if (
        type(prepared) is not PreparedSource
        or caller.settings is None
        or caller.settings.provider_root != prepared.provider_root
    ):
        raise Conflict()
    item, version, versions = readable_version(session, caller, prepared.item, prepared.version)
    if item["deleted_at"] is not None or not storage.provider_readable(
        session,
        caller,
        {"mission_key": prepared.source_mission, "silo": prepared.source_silo},
    ):
        raise NotFound()
    if (
        versions[-1]["version"] != prepared.version
        or version["id"] != prepared.version_id
        or bytes(version["sha256"]) != prepared.version_sha256
        or (
            item["path"],
            item["title"],
            item["mission_key"],
            item["silo"],
            item["stage"],
            item["classification"],
            item["simulated"],
        )
        != (
            prepared.path,
            prepared.title,
            prepared.mission,
            prepared.silo,
            prepared.stage,
            prepared.classification,
            prepared.simulated,
        )
    ):
        raise Conflict()
    rows = storage.blocks(session, version["id"], prepared.block, prepared.block)
    if not rows:
        raise Conflict()
    block = rows[0]
    if (
        block["id"] != prepared.block_id
        or block["locator"] != prepared.block_locator
        or bytes(block["text_sha256"]) != prepared.block_sha256
        or block["heading_path"] != prepared.heading_path
        or block["text"] != prepared.text
    ):
        raise Conflict()
    actual = quote(block["text"], prepared.locator, block["locator"])
    if actual is None or hashlib.sha256(actual.encode()).digest() != prepared.quote_sha256:
        raise Conflict()


def original(caller: Caller, request, attachment: bool) -> Response:
    import hashlib

    sessions, settings = scope(caller)
    provider = source_provider(settings)
    live = source_item(provider, request.item)
    with unit_of_work(sessions, read_only=True) as session:
        item, version, _ = readable_source_version(
            session, caller, request.item, request.version, live
        )
    content = source_content(provider, request.item, request.version)
    live = source_item(provider, request.item)
    with unit_of_work(sessions, read_only=True) as session:
        item, version, _ = readable_source_version(
            session, caller, request.item, request.version, live
        )
    if hashlib.sha256(content).digest() != bytes(version["sha256"]):
        raise NotFound()
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
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


def _revision_source(caller: Caller, key: str, version: int | None):
    """The readable source record and version a revision belongs to; checks access."""
    sessions, settings = scope(caller)
    live = source_item(source_provider(settings), key)
    with unit_of_work(sessions, read_only=True) as session:
        item, selected, _ = readable_source_version(session, caller, key, version, live)
    return sessions, item, selected


def _revision(row, item) -> dict:
    return {
        key: row[key]
        for key in ("item", "revision", "source_version", "file_name", "media_type", "size_bytes")
    } | {
        "content_sha256": bytes(row["content_sha256"]).hex(),
        "author_id": row["author_id"],
        "created_at": row["created_at"].isoformat(),
        "simulated": item["simulated"],
        "classification": item["classification"],
    }


def create_revision(caller: Caller, request) -> dict:
    import hashlib
    import re

    name = request.file_name
    if name in {".", ".."} or any(c in name for c in "/\\") or any(ord(c) < 32 for c in name):
        raise InvalidInput()
    if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", request.media_type):
        raise InvalidInput()
    sessions, item, selected = _revision_source(caller, request.item, request.version)
    with unit_of_work(sessions) as session:
        row = storage.add_revision(
            session,
            {
                "item": request.item,
                "source_version": selected["version"],
                "file_name": name,
                "media_type": request.media_type,
                "size_bytes": len(request.content),
                "content_sha256": hashlib.sha256(request.content).digest(),
                "bytes": request.content,
                "author_id": caller.user_id,
            },
        )
    return _revision(row, item)


def list_revisions(caller: Caller, key: str) -> list[dict]:
    sessions, item, _ = _revision_source(caller, key, None)
    with unit_of_work(sessions, read_only=True) as session:
        return [_revision(row, item) for row in storage.revisions(session, key)]


def read_revision(caller: Caller, request) -> dict:
    sessions, item, _ = _revision_source(caller, request.item, None)
    with unit_of_work(sessions, read_only=True) as session:
        row = storage.revision(session, request.item, request.revision)
    if row is None:
        raise NotFound()
    return _revision(row, item)


def export_revision(caller: Caller, request) -> Response:
    sessions, _, _ = _revision_source(caller, request.item, None)
    with unit_of_work(sessions, read_only=True) as session:
        row = storage.revision(session, request.item, request.revision, content=True)
    if row is None:
        raise NotFound()
    name = row["file_name"].replace('"', "")
    return Response(
        bytes(row["bytes"]),
        media_type=row["media_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )
