from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .models import Citation
from .store import DocumentStore
from .timeutil import dump_datetime, load_datetime
from .visibility import VisibilityScope

_MAX_TEXT = 500
_MAX_TERM = 120
_MAX_LIST = 50


@dataclass(frozen=True)
class TagFilter:
    label_key: str
    label_value: str


@dataclass(frozen=True)
class DocumentQuery:
    missions: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    silos: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()
    classifications: tuple[str, ...] = ()
    synthetic: bool | None = None
    text: str | None = None
    tags: tuple[TagFilter, ...] = ()
    known_at: datetime | None = None
    version_mode: Literal["latest", "all"] = "latest"
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    version_id: str
    sequence: int
    name: str
    mission: str
    stage: str | None
    silo: str | None
    recorded_at: datetime
    content_hash: str
    citation: Citation


@dataclass(frozen=True)
class DocumentPage:
    total_visible_versions: int
    records: tuple[DocumentRecord, ...]


@dataclass(frozen=True)
class TimelinePoint:
    version_id: str
    sequence: int
    acquired_at: datetime
    current_mission: str
    current_stage: str | None
    current_silo: str | None


@dataclass(frozen=True)
class FieldDescriptor:
    name: str
    description: str
    type: str
    operators: tuple[str, ...]


FIELD_CATALOG: tuple[FieldDescriptor, ...] = (
    FieldDescriptor(
        "missions",
        "Mission or project identity that owns a document; exact membership filter",
        "tuple[str]",
        ("in",),
    ),
    FieldDescriptor(
        "providers",
        "Source repository or external document provider, such as local files or OpenText",
        "tuple[str]",
        ("in",),
    ),
    FieldDescriptor(
        "silos",
        "Current engineering discipline or knowledge domain, such as power, thermal or program",
        "tuple[str]",
        ("in",),
    ),
    FieldDescriptor(
        "stages",
        "Current project life cycle phase or review gate, such as planning, design, "
        "testing or operations",
        "tuple[str]",
        ("in",),
    ),
    FieldDescriptor(
        "classifications",
        "Document information classification or distribution marking; current "
        "permission is checked independently",
        "tuple[str]",
        ("in",),
    ),
    FieldDescriptor(
        "synthetic",
        "Whether the document is a fictional generated or simulated example rather "
        "than supplied real content",
        "bool|None",
        ("eq",),
    ),
    FieldDescriptor(
        "text",
        "Find literal words in document names and extracted body or representation "
        "text at the selected knowledge time",
        "str|None",
        ("contains",),
    ),
    FieldDescriptor(
        "tags",
        "Exact custom label key and value assigned to the document or selected "
        "version before the knowledge cutoff",
        "tuple[TagFilter]",
        ("contains",),
    ),
    FieldDescriptor(
        "known_at",
        "What information was recorded or known by a date; source acquisition, "
        "extraction and tag timestamps",
        "datetime|None",
        ("lte",),
    ),
    FieldDescriptor(
        "version_mode",
        "Select the latest eligible version or explicitly include historical source versions",
        "literal",
        ("eq",),
    ),
)


def query_documents(store: DocumentStore, principal: str, query: DocumentQuery) -> DocumentPage:
    if not isinstance(store, DocumentStore):
        raise TypeError("store must be a DocumentStore")
    if not isinstance(principal, str) or not principal.strip():
        raise TypeError("principal must be non-empty text")
    _validate_query(query)

    scope = VisibilityScope(principal, query.version_mode, query.known_at)
    version_filter, version_parameters = scope.versions()
    clauses: list[str] = [version_filter]
    params: list[object] = list(version_parameters)
    _append_in_filter("d.mission", query.missions, clauses, params)
    _append_in_filter("d.provider", query.providers, clauses, params)
    _append_in_filter("d.silo", query.silos, clauses, params)
    _append_in_filter("d.stage", query.stages, clauses, params)
    _append_in_filter("d.classification", query.classifications, clauses, params)
    if query.synthetic is not None:
        clauses.append("d.synthetic = ?")
        params.append(1 if query.synthetic else 0)
    if query.text:
        like = _escape_like(query.text)
        block_scope, block_parameters = scope.extractions("e")
        representation_scope, representation_parameters = scope.extractions("e2")
        representation_time, representation_time_parameters = scope.created("r")
        clauses.append(
            "(d.name LIKE ? ESCAPE '\\' OR "
            "EXISTS (SELECT 1 FROM extractions e JOIN blocks b "
            "ON b.version_id=e.version_id AND b.extraction_id=e.extraction_id "
            "WHERE e.version_id=v.version_id AND "
            + block_scope
            + " AND b.text LIKE ? ESCAPE '\\') OR "
            "EXISTS (SELECT 1 FROM extractions e2 JOIN representations r "
            "ON r.version_id=e2.version_id AND r.extraction_id=e2.extraction_id "
            "WHERE e2.version_id=v.version_id AND "
            + representation_scope
            + f" AND {representation_time} AND r.text LIKE ? ESCAPE '\\'))"
        )
        params.extend(
            [
                like,
                *block_parameters,
                like,
                *representation_parameters,
                *representation_time_parameters,
                like,
            ]
        )
    for tag in query.tags:
        clauses.append(
            "EXISTS (SELECT 1 FROM tag_assignments ta WHERE ta.document_id = d.document_id "
            "AND (ta.version_id = v.version_id OR ta.version_id IS NULL) "
            "AND ta.label_key = ? AND ta.label_value = ?"
            + (" AND ta.assigned_at <= ?" if query.known_at is not None else "")
            + ")"
        )
        params.extend([tag.label_key, tag.label_value])
        if query.known_at is not None:
            params.append(dump_datetime(query.known_at))

    where_sql = " AND ".join(clauses)
    cte = (
        "WITH eligible AS ("
        "SELECT d.document_id, d.name, d.mission, d.stage, d.silo, v.version_id, "
        "v.sequence, v.content_hash, v.created_at, d.source_location, d.provider, "
        "d.remote_id, v.provider_version, v.source_key "
        "FROM documents d "
        "JOIN mission_grants g ON g.mission = d.mission "
        "JOIN doc_versions v ON v.document_id = d.document_id "
        f"WHERE {where_sql}"
        ")"
    )
    scope = cte + " SELECT * FROM eligible"

    with store.read_connection() as connection:
        connection.execute("BEGIN")
        count_row = connection.execute(
            f"SELECT COUNT(*) AS c FROM ({scope}) s",
            tuple(params),
        ).fetchone()
        page_rows = connection.execute(
            f"SELECT * FROM ({scope}) s ORDER BY s.document_id, "
            "s.source_location, s.version_id LIMIT ? OFFSET ?",
            tuple([*params, query.limit, query.offset]),
        ).fetchall()

    records: list[DocumentRecord] = []
    for row in page_rows:
        version_id = row["version_id"]
        records.append(
            DocumentRecord(
                document_id=row["document_id"],
                version_id=version_id,
                sequence=row["sequence"],
                name=row["name"],
                mission=row["mission"],
                stage=row["stage"],
                silo=row["silo"],
                recorded_at=load_datetime(row["created_at"]),
                content_hash=row["content_hash"],
                citation=Citation(
                    provider=row["provider"],
                    item_id=row["remote_id"] or row["document_id"],
                    version=row["provider_version"]
                    or f"sha256:{row['content_hash']}#{row['sequence']}",
                    block_id=None,
                    location=None,
                    content_hash=row["content_hash"],
                    source_key=row["source_key"],
                ),
            )
        )
    return DocumentPage(total_visible_versions=int(count_row["c"]), records=tuple(records))


def version_timeline(
    store: DocumentStore,
    principal: str,
    document_id: str,
    known_at: datetime | None = None,
) -> tuple[TimelinePoint, ...]:
    if not isinstance(principal, str) or not principal.strip():
        raise TypeError("principal must be non-empty text")
    if not isinstance(document_id, str) or not document_id.strip():
        raise TypeError("document_id must be non-empty text")
    if known_at is not None:
        _require_aware_datetime(known_at, "known_at")

    sql = (
        "SELECT v.version_id, v.sequence, v.created_at, d.mission, d.stage, d.silo "
        "FROM documents d "
        "JOIN mission_grants g ON g.mission = d.mission "
        "JOIN doc_versions v ON v.document_id = d.document_id "
        "WHERE d.document_id = ? AND d.remote_status = 'active' "
        "AND g.principal = ? AND g.can_read = 1"
    )
    params: list[object] = [document_id, principal]
    if known_at is not None:
        sql += " AND v.created_at <= ?"
        params.append(dump_datetime(known_at))
    sql += " ORDER BY v.sequence, v.version_id"

    with store.read_connection() as connection:
        rows = connection.execute(sql, tuple(params)).fetchall()
    return tuple(
        TimelinePoint(
            version_id=r["version_id"],
            sequence=r["sequence"],
            acquired_at=load_datetime(r["created_at"]),
            current_mission=r["mission"],
            current_stage=r["stage"],
            current_silo=r["silo"],
        )
        for r in rows
    )


CATALOG_HASH = hashlib.sha256(
    json.dumps(
        [(field.name, field.description, field.type, field.operators) for field in FIELD_CATALOG],
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
).hexdigest()


@dataclass(frozen=True)
class FieldVector:
    vector: tuple[float, ...]
    model: str
    dimensions: int
    recipe: str
    catalog_hash: str = CATALOG_HASH


def rank_fields(
    query_vector: Sequence[float],
    field_vectors: Mapping[str, FieldVector],
    *,
    model: str,
    dimensions: int,
    recipe: str,
) -> tuple[tuple[str, float], ...]:
    if (
        not isinstance(model, str)
        or not model.strip()
        or not isinstance(recipe, str)
        or not recipe.strip()
    ):
        raise TypeError("model and recipe must be nonempty strings")
    if type(dimensions) is not int or not 1 <= dimensions <= 8192:
        raise ValueError("invalid_dimension")
    if not isinstance(field_vectors, Mapping):
        raise TypeError("invalid_field_vectors")
    q = _unit_vector(query_vector, dimensions)
    names = {field.name for field in FIELD_CATALOG}
    scored = []
    for name, field in field_vectors.items():
        if name not in names or not isinstance(field, FieldVector):
            raise ValueError("unknown_field")
        if (field.model, field.dimensions, field.recipe, field.catalog_hash) != (
            model,
            dimensions,
            recipe,
            CATALOG_HASH,
        ):
            raise ValueError("embedding_space_mismatch")
        vector = _unit_vector(field.vector, dimensions)
        score = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(q, vector, strict=True))))
        scored.append((name, score))
    return tuple(sorted(scored, key=lambda item: (-item[1], item[0])))


def _unit_vector(values, dimensions):
    vector = _validated_vector(values, dimensions, "vector")
    scale = max(abs(value) for value in vector)
    if scale == 0:
        raise ValueError("zero_vector")
    scaled = tuple(value / scale for value in vector)
    norm = math.sqrt(math.fsum(value * value for value in scaled))
    return tuple(value / norm for value in scaled)


def _validate_query(query: DocumentQuery) -> None:
    if not isinstance(query, DocumentQuery):
        raise TypeError("query must be DocumentQuery")
    for name in ("missions", "providers", "silos", "stages", "classifications"):
        _validate_str_tuple(getattr(query, name), name)
    if query.synthetic is not None and not isinstance(query.synthetic, bool):
        raise TypeError("synthetic must be bool|None")
    if query.text is not None:
        if not isinstance(query.text, str):
            raise TypeError("text must be str|None")
        if len(query.text) > _MAX_TEXT:
            raise ValueError("text too long")
    if not isinstance(query.tags, tuple) or len(query.tags) > _MAX_LIST:
        raise TypeError("tags must be tuple[TagFilter]")
    for tag in query.tags:
        if not isinstance(tag, TagFilter):
            raise TypeError("tags must be tuple[TagFilter]")
        _validate_term(tag.label_key, "label_key")
        _validate_term(tag.label_value, "label_value")
    if query.known_at is not None:
        _require_aware_datetime(query.known_at, "known_at")
    if not isinstance(query.version_mode, str) or query.version_mode not in {"latest", "all"}:
        raise ValueError("version_mode must be latest|all")
    if isinstance(query.limit, bool) or not isinstance(query.limit, int):
        raise TypeError("limit must be int")
    if isinstance(query.offset, bool) or not isinstance(query.offset, int):
        raise TypeError("offset must be int")
    if not (1 <= query.limit <= 100):
        raise ValueError("limit out of range")
    if not (0 <= query.offset <= 10000):
        raise ValueError("offset out of range")


def _validate_str_tuple(values: tuple[str, ...], name: str) -> None:
    if not isinstance(values, tuple) or len(values) > _MAX_LIST:
        raise TypeError(f"{name} must be tuple[str]")
    for item in values:
        _validate_term(item, name)


def _validate_term(value: str, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} entries must be text")
    if not value.strip():
        raise ValueError(f"{name} entries must be non-empty")
    if len(value) > _MAX_TERM:
        raise ValueError(f"{name} entry too long")


def _append_in_filter(
    column: str, values: tuple[str, ...], clauses: list[str], params: list[object]
) -> None:
    if not values:
        return
    placeholders = ",".join("?" for _ in values)
    clauses.append(f"{column} IN ({placeholders})")
    params.extend(values)


def _escape_like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _require_aware_datetime(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError(f"{name} must be timezone-aware datetime")


def _validated_vector(values: Sequence[float], dimensions: int, name: str) -> tuple[float, ...]:
    if not isinstance(values, Sequence):
        raise TypeError(f"{name} must be a numeric sequence")
    vec: list[float] = []
    for item in values:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise TypeError(f"{name} must contain finite numbers")
        try:
            v = float(item)
        except OverflowError:
            raise ValueError("invalid_vector") from None
        if not math.isfinite(v):
            raise ValueError(f"{name} contains non-finite values")
        vec.append(v)
    if len(vec) != dimensions:
        raise ValueError(f"{name} length must equal dimensions")
    return tuple(vec)
