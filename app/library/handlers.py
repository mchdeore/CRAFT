"""Library shapes and the sole entry points for readable source operations."""

from typing import Annotated, Literal

import msgspec
from starlette.responses import Response

from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import RawFile, Shape
from app.library import service

Classification = Literal["unclassified", "protected-a", "protected-b"]


class CitationHandle(Shape):
    item: str
    version: int
    block: int
    locator: str
    quote_sha256: str
    simulated: bool


class Tags(Shape):
    mission: str | None
    silo: str | None
    stage: str | None


class LibrarySearch(Shape):
    query: Annotated[str, msgspec.Meta(min_length=1, max_length=500)]
    missions: list[str] | None = None
    silos: list[str] | None = None
    stages: list[str] | None = None
    kinds: list[Literal["chunk", "document"]] | None = None
    limit: Annotated[int, msgspec.Meta(ge=1, le=8)] = 8


class ChunkText(Shape, omit_defaults=True):
    handle: CitationHandle
    heading_path: str
    text: str
    score: float | None = None


class LibraryHit(Shape):
    kind: Literal["chunk", "document"]
    handle: CitationHandle
    title: str
    heading_path: str
    text: str
    score: float
    tags: Tags
    classification: Classification
    before: ChunkText | None
    after: ChunkText | None


class LibraryHits(Shape):
    hits: list[LibraryHit]
    truncated: bool


class DocumentRef(Shape):
    item: str
    version: int | None = None


class Outline(Shape):
    block: int
    heading_path: str


class Version(Shape):
    version: int
    sha256: str
    indexed_at: str


class LibraryDocument(Shape):
    item: str
    version: int
    title: str
    path: str
    tags: Tags
    summary: str
    summary_kind: Literal["extract", "generated"]
    outline: list[Outline]
    versions: list[Version]
    simulated: bool
    classification: Classification
    deleted: bool


class ChunkRange(Shape):
    item: str
    version: int
    from_block: int
    to_block: int


class ExploreInput(Shape):
    handle: CitationHandle
    before: Annotated[int, msgspec.Meta(ge=0, le=10)] = 2
    after: Annotated[int, msgspec.Meta(ge=0, le=10)] = 2
    query: str | None = None


class ChunkPage(Shape):
    chunks: list[ChunkText]
    truncated: bool


class OriginalRef(Shape):
    item: str
    version: int
    locator: str | None = None


@operation(SIGNED_IN, post="/library/search", tool=True)
def search(caller: Caller, request: LibrarySearch) -> LibraryHits:
    """Find readable source blocks. Use for document questions; for example radiator margin."""
    return service.search(caller, request)


@operation(SIGNED_IN, get="/library/documents/{item}", tool=True)
def get_document(caller: Caller, request: DocumentRef) -> LibraryDocument:
    """Read a document outline and versions. Use a known item, such as itm_000000000001."""
    return service.document(caller, request)


@operation(SIGNED_IN, get="/library/documents/{item}/chunks", tool=True)
def read_chunks(caller: Caller, request: ChunkRange) -> ChunkPage:
    """Read up to twenty source blocks. Use after search; for example blocks 1 through 3."""
    return service.read_chunks(caller, request)


@operation(SIGNED_IN, post="/library/explore", tool=True)
def explore(caller: Caller, request: ExploreInput) -> ChunkPage:
    """Read blocks around a citation. Use for context; for example two blocks before and after."""
    return service.explore(caller, request)


@operation(SIGNED_IN, get="/library/documents/{item}/preview")
def preview(caller: Caller, request: OriginalRef) -> Response:
    """Open a readable original version inline."""
    return service.original(caller, request, False)


@operation(SIGNED_IN, get="/library/documents/{item}/download")
def download(caller: Caller, request: OriginalRef) -> Response:
    """Download a readable original version."""
    return service.original(caller, request, True)


def index_provider(settings, rebuild: bool = False) -> dict[str, int]:
    """Run local owner indexing for command and seed orchestration."""
    from app.library.index import build

    return build(settings, rebuild)


def convert_source(content: bytes, filename: str):
    """Convert supplied source bytes for local seed and indexing maintenance."""
    from app.library.convert import convert

    return convert(content, filename)


class InspectedBlock(Shape):
    block: int
    kind: str
    heading_path: str
    locator: str
    text: str
    quote_sha256: str


class InspectedFile(Shape):
    file_name: str
    media_type: str
    sha256: str
    simulated: bool
    blocks: list[InspectedBlock]


@operation(SIGNED_IN)
def inspect_file(caller: Caller, request: RawFile) -> InspectedFile:
    """Inspect owned bytes in memory without indexing or storing an original."""
    return service.inspect_file(request)


def citation_status(caller: Caller, handle: CitationHandle):
    """Read citation status from authorized library records without re-reading a file."""
    document = get_document(caller, DocumentRef(item=handle.item, version=handle.version))
    return (
        "withdrawn"
        if document.deleted
        else "newer_version"
        if any(v.version > handle.version for v in document.versions)
        else "current"
    )


def citation_quote(caller: Caller, handle: CitationHandle) -> str:
    """Read the authorized cached block or source row named by a citation."""
    import hashlib
    import re

    from app.infrastructure.errors import NotFound

    page = read_chunks(
        caller,
        ChunkRange(
            item=handle.item, version=handle.version, from_block=handle.block, to_block=handle.block
        ),
    )
    if not page.chunks:
        raise NotFound()
    chunk = page.chunks[0]
    base, marker, number = handle.locator.rpartition(";row=")
    if marker:
        if base != chunk.handle.locator or not number.isdigit():
            raise NotFound()
        window = re.search(r";rows=(\d+)-(\d+)", base)
        region = re.search(r";range=[A-Z]+(\d+):", base)
        ordinal = int(number) - (int(region[1]) if region else 0)
        first = int(window[1]) if window else 1
        index = 2 + ordinal - first
        lines = chunk.text.splitlines()
        if index < 2 or index >= len(lines):
            raise NotFound()
        quote = lines[index]
    else:
        if handle.locator != chunk.handle.locator:
            raise NotFound()
        quote = chunk.text
    if hashlib.sha256(quote.encode()).hexdigest() != handle.quote_sha256:
        raise NotFound()
    return quote
