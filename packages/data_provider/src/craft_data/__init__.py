"""Independent local document/provider and datacube prototype."""

from .cube import (
    FakeApprovalBoundary,
    convert_unit,
    create_candidate_batch,
    insert_cube_value,
    publish_candidate_batch,
    query_cube,
)
from .embeddings import FakeEmbedding, cosine_similarity, get_embedding, insert_embedding
from .models import (
    ApprovalDecision,
    BlockInput,
    CandidateValueInput,
    CubeQuery,
    CubeValueInput,
    ExternalRecord,
    SearchQuery,
    StorageMode,
    ValueState,
)
from .relations import (
    add_evidence_edge,
    add_similarity_candidate,
    add_structural_edge,
    export_graph,
    list_relations,
)
from .search import search
from .simulator import SimulatedItem, SimulatedTenant, SimulatedVersion, TenantReconciler
from .store import DocumentStore, ExternalOriginalUnavailableError, VersionConflictError

__all__ = [
    "ApprovalDecision",
    "BlockInput",
    "CandidateValueInput",
    "CubeQuery",
    "CubeValueInput",
    "DocumentStore",
    "ExternalOriginalUnavailableError",
    "ExternalRecord",
    "FakeApprovalBoundary",
    "FakeEmbedding",
    "SearchQuery",
    "SimulatedItem",
    "SimulatedTenant",
    "SimulatedVersion",
    "StorageMode",
    "TenantReconciler",
    "ValueState",
    "VersionConflictError",
    "add_evidence_edge",
    "add_similarity_candidate",
    "add_structural_edge",
    "convert_unit",
    "cosine_similarity",
    "create_candidate_batch",
    "export_graph",
    "get_embedding",
    "insert_cube_value",
    "insert_embedding",
    "list_relations",
    "publish_candidate_batch",
    "query_cube",
    "search",
]
