"""Library shapes and the sole entry points for readable source operations."""

from typing import Annotated, Literal

import msgspec
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.infrastructure.access import SIGNED_IN, Caller, require
from app.infrastructure.operations import observe, operation
from app.infrastructure.shapes import RawFile, Shape
from app.library import service

PreparedSource = service.PreparedSource

Classification = Literal["unclassified", "protected-a", "protected-b"]
Facet = Literal["purpose", "design", "operations", "program", "cost_and_risk"]


class DocumentList(Shape):
    limit: Annotated[int, msgspec.Meta(ge=1, le=50)] = 25


class CatalogDocument(Shape):
    item: Annotated[str, msgspec.Meta(pattern=r"^itm_[0-9a-f]{12}$")]
    title: Annotated[str, msgspec.Meta(min_length=1, max_length=200)]
    version: Annotated[int, msgspec.Meta(ge=1)]
    classification: Classification
    simulated: bool


class DocumentCatalog(Shape):
    documents: list[CatalogDocument]
    truncated: bool


@operation(SIGNED_IN, get="/library/documents", tool=True)
def list_documents(caller: Caller, request: DocumentList) -> DocumentCatalog:
    """List currently readable indexed documents within bounded scan and result limits."""
    return service.documents(caller, request)


class FacetRequest(Shape):
    missions: list[str]
    facets: list[Facet]


class FacetEvidence(Shape):
    mission: str
    facet: Facet
    text: str
    sources: list[str]
    vector: Annotated[list[float], msgspec.Meta(min_length=1536, max_length=1536)] | None


@operation(SIGNED_IN)
def read_facets(caller: Caller, request: FacetRequest) -> list[FacetEvidence]:
    """Read private facet evidence only when every live source remains readable."""
    return service.read_facets(caller, request)


class CitationHandle(Shape):
    item: str
    version: int
    block: int
    locator: str
    quote_sha256: str
    simulated: bool


@observe("library.prepare_source")
def prepare_source(caller: Caller, handle: CitationHandle) -> PreparedSource:
    """Sample current source evidence for server-owned approved execution."""
    require(caller, SIGNED_IN)
    return service.prepare_source(caller, handle)


@observe("library.validate_prepared_source")
def validate_prepared_source(caller: Caller, session: Session, prepared: PreparedSource) -> None:
    """Recheck indexed provenance and current grants without provider access."""
    require(caller, SIGNED_IN)
    service.validate_prepared_source(session, caller, prepared)


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


JobState = Literal[
    "queued",
    "started",
    "deferred",
    "scheduled",
    "finished",
    "failed",
    "stopped",
    "canceled",
    "unknown",
]


class OriginalRef(Shape):
    item: str
    version: int
    locator: str | None = None


class CitationCheck(Shape):
    handle: CitationHandle


class CitationStatus(Shape):
    status: Literal["current", "changed", "newer_version", "withdrawn"]
    latest_version: int


class CompareVersions(Shape):
    item: str
    from_version: Annotated[int, msgspec.Meta(ge=1)]
    to_version: Annotated[int, msgspec.Meta(ge=1)]


class DiffBlock(Shape):
    change: Literal["added", "removed", "changed", "moved"]
    from_block: int | None
    to_block: int | None


class VersionDiff(Shape):
    blocks: list[DiffBlock]
    truncated: bool


@operation(SIGNED_IN, post="/library/citations/check", tool=True)
def check_citation(caller: Caller, request: CitationCheck) -> CitationStatus:
    """Check a quote against its readable original version; for example a table-row citation."""
    return service.check_citation(caller, request)


@operation(SIGNED_IN, get="/library/documents/{item}/compare", tool=True)
def compare_versions(caller: Caller, request: CompareVersions) -> VersionDiff:
    """Compare readable versions by block hash; for example versions one and three."""
    return service.compare_versions(caller, request)


@operation(SIGNED_IN, post="/library/search", tool=True)
def search(caller: Caller, request: LibrarySearch) -> LibraryHits:
    """Find ranked readable passages and summaries, not whole-document content.

    Group hits by item/version and show titles with a sentence preview for selection.
    Refine the query when choices are rejected. Ranking scores are not confidence.
    """
    return service.search(caller, request)


@operation(SIGNED_IN, get="/library/documents/{item}", tool=True)
def get_document(caller: Caller, request: DocumentRef) -> LibraryDocument:
    """Read exact document metadata, the full ordered outline and indexed versions.

    Pin item/version before reading all blocks. The stored summary may be an extract.
    Label the selected version's indexedAt as Last indexed, not provider sync time.
    """
    return service.document(caller, request)


@operation(SIGNED_IN, get="/library/documents/{item}/chunks", tool=True)
def read_chunks(caller: Caller, request: ChunkRange) -> ChunkPage:
    """Read an inclusive range of at most twenty blocks for one exact item/version.

    For whole-document text, cover get_document's outline with successive ranges.
    Check returned block identities for gaps; quote hashes belong to the returned text.
    """
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


def index_provider(settings, rebuild: bool = False, *, enrich_ocr: bool = False) -> dict[str, int]:
    """Run local owner indexing for command and seed orchestration."""
    from app.library.index import build

    return build(settings, rebuild, enrich_ocr=enrich_ocr)


class IndexJobRequest(Shape):
    rebuild: bool = False
    enrich_ocr: bool = False


class IndexJobRef(Shape):
    job_id: str


class OcrJobRequest(Shape):
    item: str
    version: Annotated[int, msgspec.Meta(gt=0)]


class IndexJobPageRequest(Shape):
    status: Literal[
        "queued", "started", "deferred", "scheduled", "finished", "failed", "stopped", "canceled"
    ] = "queued"
    offset: Annotated[int, msgspec.Meta(ge=0, le=100_000)] = 0
    limit: Annotated[int, msgspec.Meta(ge=1, le=100)] = 25


class IndexJob(Shape, omit_defaults=True):
    job_id: str
    status: JobState
    queue: str
    rebuild: bool
    enrich_ocr: bool
    kind: Literal["index", "ocr"] = "index"
    item: str | None = None
    version: int | None = None
    force: bool = False
    requested_by: str | None = None
    retry_of: str | None = None
    enqueued_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    result: dict[str, str | int] | None = None
    error: str | None = None


class QueueWorker(Shape, omit_defaults=True):
    name: str
    state: str
    queues: list[str]
    successful_jobs: int
    failed_jobs: int
    last_heartbeat: str | None = None


class IndexJobPage(Shape):
    jobs: list[IndexJob]
    next_offset: int | None


class IndexQueueHealth(Shape):
    queue: str
    queued: int
    started: int
    deferred: int
    scheduled: int
    failed: int
    finished: int
    canceled: int
    workers: list[QueueWorker]


@operation("library.manage", post="/library/index/jobs")
def enqueue_index_job(caller: Caller, request: IndexJobRequest) -> IndexJob:
    """Queue one library indexing build for asynchronous worker execution."""
    from app.library.queue import enqueue_index_job

    return IndexJob(**enqueue_index_job(caller, request.rebuild, enrich_ocr=request.enrich_ocr))


@operation("library.manage", get="/library/index/jobs/{jobId}")
def read_index_job(caller: Caller, request: IndexJobRef) -> IndexJob:
    """Read one queued index job status, result and failure details."""
    from app.library.queue import read_index_job

    return IndexJob(**read_index_job(caller, request.job_id))


@operation("library.manage", get="/library/index/jobs")
def list_index_jobs(caller: Caller, request: IndexJobPageRequest) -> IndexJobPage:
    """Read a bounded raw queue or registry page without registry maintenance."""
    from app.library.queue import list_index_jobs

    data = list_index_jobs(caller, request.status, request.offset, request.limit)
    return IndexJobPage(
        jobs=[IndexJob(**job) for job in data["jobs"]], next_offset=data["next_offset"]
    )


@operation("library.manage", post="/library/index/jobs/{jobId}/retry")
def retry_index_job(caller: Caller, request: IndexJobRef) -> IndexJob:
    """Retry one failed, canceled or stopped index job as a new queued run."""
    from app.library.queue import retry_index_job

    return IndexJob(**retry_index_job(caller, request.job_id))


@operation("library.manage", post="/library/index/jobs/{jobId}/cancel")
def cancel_index_job(caller: Caller, request: IndexJobRef) -> IndexJob:
    """Cancel one queued index job before a worker starts it."""
    from app.library.queue import cancel_index_job

    return IndexJob(**cancel_index_job(caller, request.job_id))


@operation("library.manage", get="/library/index/queue")
def read_index_queue(caller: Caller) -> IndexQueueHealth:
    """Show queue depth, terminal job counts and active worker heartbeats."""
    from app.library.queue import queue_health

    data = queue_health(caller)
    data["workers"] = [QueueWorker(**worker) for worker in data["workers"]]
    return IndexQueueHealth(**data)


@operation("library.manage", post="/library/documents/{item}/versions/{version}/ocr")
def recover_ocr(caller: Caller, request: OcrJobRequest) -> IndexJob:
    """Explicitly re-run OCR for a readable version, including expired failed jobs."""
    from app.library.enrichment import recover_version

    return IndexJob(**recover_version(caller, request.item, request.version))


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
    """Use the checked fresh-source operation for cross-feature citation status."""
    return check_citation(caller, CitationCheck(handle=handle)).status


def citation_quote(caller: Caller, handle: CitationHandle) -> str:
    """Read the authorized cached block or source row named by a citation."""
    import hashlib

    from app.infrastructure.errors import NotFound
    from app.library.citation import quote as locate_quote

    page = read_chunks(
        caller,
        ChunkRange(
            item=handle.item, version=handle.version, from_block=handle.block, to_block=handle.block
        ),
    )
    if not page.chunks:
        raise NotFound()
    chunk = page.chunks[0]
    quote = locate_quote(chunk.text, handle.locator, chunk.handle.locator)
    if quote is None:
        raise NotFound()
    if hashlib.sha256(quote.encode()).hexdigest() != handle.quote_sha256:
        raise NotFound()
    return quote
