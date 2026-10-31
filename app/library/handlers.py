"""Library shapes and the sole entry points for readable source operations."""

from typing import Annotated, Literal
from uuid import UUID

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
CitationState = Literal["current", "changed", "newer_version", "withdrawn"]


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


class OriginalRef(Shape):
    item: str
    version: int
    locator: str | None = None


class CitationCheck(Shape):
    handle: CitationHandle


class CitationStatus(Shape):
    status: CitationState
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


class RelationsRequest(Shape):
    item: Annotated[str, msgspec.Meta(pattern=r"^itm_[0-9a-f]{12}$")] | None = None
    mission: Annotated[str, msgspec.Meta(min_length=1, max_length=100)] | None = None


class GraphNode(Shape, omit_defaults=True):
    id: str
    kind: Literal["file", "document", "version", "value", "facet", "mission"]
    label: str
    linked: bool
    missing: bool
    path: str | None = None
    item: str | None = None
    group: str | None = None


class GraphEdge(Shape, omit_defaults=True):
    id: str
    from_node: str = msgspec.field(name="from")
    to_node: str = msgspec.field(name="to")
    relation: str
    note: str | None = None


class GraphGroup(Shape, omit_defaults=True):
    id: str
    label: str
    parent: str | None = None


class Graph(Shape):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    groups: list[GraphGroup]


@operation(SIGNED_IN, get="/library/relations", tool=True)
def relations(caller: Caller, request: RelationsRequest) -> Graph:
    """Show how readable documents relate: versions, citing cube values, facets and mission.

    Give an item for its neighbourhood or a mission for all its documents; for example one item.
    """
    return service.relations(caller, request)


@operation(SIGNED_IN, get="/library/documents/{item}/preview")
def preview(caller: Caller, request: OriginalRef) -> Response:
    """Open a readable original version inline."""
    return service.original(caller, request, False)


@operation(SIGNED_IN, get="/library/documents/{item}/download")
def download(caller: Caller, request: OriginalRef) -> Response:
    """Download a readable original version."""
    return service.original(caller, request, True)


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


@observe("library.citation_evidence")
def citation_evidence(caller: Caller, handle: CitationHandle) -> tuple[CitationState, str | None]:
    """A citation's status and the quote its stored block holds (None when the source is
    withdrawn or the block doesn't hold the quote), from one check."""
    require(caller, SIGNED_IN)
    status, _, _, quote = service.checked_citation(caller, handle)
    return status.status, quote


class RevisionUpload(RawFile, kw_only=True):
    item: str
    version: Annotated[int, msgspec.Meta(ge=1)]


class RevisionList(Shape):
    item: str


class RevisionRef(Shape):
    item: str
    revision: Annotated[int, msgspec.Meta(ge=1)]


class Revision(Shape):
    item: str
    revision: int
    source_version: int
    file_name: str
    media_type: str
    size_bytes: int
    content_sha256: str
    author_id: UUID
    created_at: str
    simulated: bool
    classification: str


@operation(SIGNED_IN, post="/library/documents/{item}/revisions")
def create_revision(caller: Caller, request: RevisionUpload) -> Revision:
    """Save CRAFT's own full copy of a readable document, pointing at its source version.

    The source is never changed and a revision is never written back to it.
    """
    return Revision(**service.create_revision(caller, request))


@operation(SIGNED_IN, get="/library/documents/{item}/revisions")
def list_revisions(caller: Caller, request: RevisionList) -> list[Revision]:
    """List a readable document's CRAFT revisions, oldest first."""
    return [Revision(**row) for row in service.list_revisions(caller, request.item)]


@operation(SIGNED_IN, get="/library/documents/{item}/revisions/{revision}")
def read_revision(caller: Caller, request: RevisionRef) -> Revision:
    """Read one CRAFT revision's record, including the source version it came from."""
    return Revision(**service.read_revision(caller, request))


@operation(SIGNED_IN, get="/library/documents/{item}/revisions/{revision}/content")
def export_revision(caller: Caller, request: RevisionRef) -> Response:
    """Download one CRAFT revision's exact bytes as an attachment."""
    return service.export_revision(caller, request)
