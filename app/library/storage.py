"""Readable library queries use explicit sessions and never choose transactions."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, column, func, or_, select, table, text, tuple_
from sqlalchemy.orm import Session

from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.grants import readable


def clear_index(
    session: Session,
    items: Sequence[str],
    *,
    preserve_versions: bool = False,
    preserve_facets: bool = False,
    versions: Sequence[tuple[str, int]] | None = None,
) -> None:
    """Remove derived rows for these source items, preserving other providers.

    With versions, only those (item, version) pairs lose their chunks and vectors.
    """
    if not items:
        return
    documents = select(m.doc_versions.c.id).where(m.doc_versions.c.item.in_(items))
    if versions is not None:
        documents = documents.where(
            tuple_(m.doc_versions.c.item, m.doc_versions.c.version).in_(list(versions))
        )
    chunks = select(m.chunks.c.id).where(m.chunks.c.doc_version_id.in_(documents))
    facets = select(m.facet_texts.c.id).where(m.facet_texts.c.sources.overlap(list(items)))
    vector_owners = [
        and_(m.vectors.c.owner_kind == "doc_version", m.vectors.c.owner_id.in_(documents)),
        and_(m.vectors.c.owner_kind == "chunk", m.vectors.c.owner_id.in_(chunks)),
    ]
    if not preserve_facets:
        vector_owners.append(
            and_(m.vectors.c.owner_kind == "facet_text", m.vectors.c.owner_id.in_(facets))
        )
    session.execute(m.vectors.delete().where(or_(*vector_owners)))
    if not preserve_facets:
        session.execute(m.facet_texts.delete().where(m.facet_texts.c.id.in_(facets)))
    session.execute(
        m.version_diffs.delete().where(
            or_(
                m.version_diffs.c.from_version.in_(documents),
                m.version_diffs.c.to_version.in_(documents),
            )
        )
    )
    session.execute(m.chunks.delete().where(m.chunks.c.id.in_(chunks)))
    if not preserve_versions:
        session.execute(m.doc_versions.delete().where(m.doc_versions.c.id.in_(documents)))


def item(session: Session, caller: Caller, key: str):
    return (
        session.execute(
            select(m.provider_items).where(m.provider_items.c.item == key, readable(caller))
        )
        .mappings()
        .first()
    )


def catalog_items(session: Session, caller: Caller):
    """Take at most 200 readable indexed IDs plus a scan-limit sentinel."""
    indexed = (
        select(m.doc_versions.c.id).where(m.doc_versions.c.item == m.provider_items.c.item).exists()
    )
    return list(
        session.execute(
            select(m.provider_items.c.item)
            .where(readable(caller), m.provider_items.c.deleted_at.is_(None), indexed)
            .order_by(m.provider_items.c.item)
            .limit(201)
        ).scalars()
    )


def versions(session: Session, key: str):
    return (
        session.execute(
            select(m.doc_versions)
            .where(m.doc_versions.c.item == key)
            .order_by(m.doc_versions.c.version)
        )
        .mappings()
        .all()
    )


def blocks(session: Session, version_id, first: int = 1, last: int = 2147483647):
    return (
        session.execute(
            select(m.chunks)
            .where(m.chunks.c.doc_version_id == version_id, m.chunks.c.block.between(first, last))
            .order_by(m.chunks.c.block)
        )
        .mappings()
        .all()
    )


def differences(session: Session, before, after):
    return list(
        session.execute(
            select(
                m.version_diffs.c.change,
                m.version_diffs.c.from_block,
                m.version_diffs.c.to_block,
            )
            .where(
                m.version_diffs.c.from_version == before,
                m.version_diffs.c.to_version == after,
            )
            .order_by(m.version_diffs.c.seq)
            .limit(501)
        ).mappings()
    )


def provider_readable(session: Session, caller: Caller, record) -> bool:
    mission = record["mission_key"]
    if mission is None or "admin" in caller.roles:
        return True
    return (
        session.execute(
            select(m.mission_grants.c.user_id)
            .where(
                m.mission_grants.c.user_id == caller.user_id,
                m.mission_grants.c.mission_key == mission,
                or_(m.mission_grants.c.silo.is_(None), m.mission_grants.c.silo == record["silo"]),
            )
            .limit(1)
        ).first()
        is not None
    )


def facet_documents(session: Session, mission, items):
    latest = (
        select(m.doc_versions.c.item, func.max(m.doc_versions.c.version).label("version"))
        .group_by(m.doc_versions.c.item)
        .subquery()
    )
    return [
        dict(row)
        for row in session.execute(
            select(
                m.provider_items.c.item,
                m.provider_items.c.title,
                m.provider_items.c.silo,
                m.provider_items.c.simulated,
                m.doc_versions.c.summary,
            )
            .join(m.doc_versions, m.doc_versions.c.item == m.provider_items.c.item)
            .join(
                latest,
                and_(
                    latest.c.item == m.doc_versions.c.item,
                    latest.c.version == m.doc_versions.c.version,
                ),
            )
            .join(m.missions, m.missions.c.key == m.provider_items.c.mission_key)
            .where(
                m.provider_items.c.mission_key == mission,
                m.provider_items.c.item.in_(items),
                m.provider_items.c.deleted_at.is_(None),
            )
            .order_by(m.provider_items.c.item)
        ).mappings()
    ]


def facet_refresh(session: Session, missions, changed_items, main_model, embedding_model):
    rows = list(
        session.execute(
            select(
                m.facet_texts.c.mission_key,
                m.facet_texts.c.sources,
                m.facet_texts.c.model,
                m.vectors.c.model.label("embedding_model"),
                m.vectors.c.recipe,
            ).outerjoin(
                m.vectors,
                and_(
                    m.vectors.c.owner_kind == "facet_text",
                    m.vectors.c.owner_id == m.facet_texts.c.id,
                ),
            )
        ).mappings()
    )
    seen = {row["mission_key"] for row in rows}
    dirty = set(missions) - seen
    for row in rows:
        if set(row["sources"]) & changed_items or (
            row["mission_key"] in missions
            and (
                row["model"] != main_model
                or row["embedding_model"] != embedding_model
                or row["recipe"] != "facet-v1"
            )
        ):
            dirty.add(row["mission_key"])
    return dirty


def replace_facets(session: Session, mission, prepared, main_model, embedding_model):
    ids = list(
        session.execute(
            select(m.facet_texts.c.id).where(m.facet_texts.c.mission_key == mission)
        ).scalars()
    )
    session.execute(
        m.vectors.delete().where(
            m.vectors.c.owner_kind == "facet_text", m.vectors.c.owner_id.in_(ids)
        )
    )
    session.execute(m.facet_texts.delete().where(m.facet_texts.c.mission_key == mission))
    for facet, (text_value, sources, vector) in prepared.items():
        identifier = session.execute(
            m.facet_texts.insert()
            .values(
                mission_key=mission, facet=facet, text=text_value, sources=sources, model=main_model
            )
            .returning(m.facet_texts.c.id)
        ).scalar_one()
        session.execute(
            m.vectors.insert().values(
                owner_kind="facet_text",
                owner_id=identifier,
                model=embedding_model,
                recipe="facet-v1",
                embedding=vector,
            )
        )
    return len(ids)


def facet_records(session: Session, missions, facets):
    return [
        dict(row)
        for row in session.execute(
            select(m.facet_texts)
            .where(m.facet_texts.c.mission_key.in_(missions), m.facet_texts.c.facet.in_(facets))
            .order_by(m.facet_texts.c.mission_key, m.facet_texts.c.facet)
        ).mappings()
    ]


def provider_records(session: Session, items):
    return {
        row["item"]: dict(row)
        for row in session.execute(
            select(m.provider_items).where(m.provider_items.c.item.in_(items))
        ).mappings()
    }


def facet_vectors(session: Session, identifiers, model):
    return {
        row["owner_id"]: [float(value) for value in row["embedding"]]
        for row in session.execute(
            select(m.vectors.c.owner_id, m.vectors.c.embedding).where(
                m.vectors.c.owner_kind == "facet_text",
                m.vectors.c.owner_id.in_(identifiers),
                m.vectors.c.model == model,
                m.vectors.c.recipe == "facet-v1",
            )
        ).mappings()
    }


def chunk_vectors(session: Session, identifiers, model):
    return {
        row["owner_id"]: [float(value) for value in row["embedding"]]
        for row in session.execute(
            select(m.vectors.c.owner_id, m.vectors.c.embedding).where(
                m.vectors.c.owner_kind == "chunk",
                m.vectors.c.owner_id.in_(identifiers),
                m.vectors.c.model == model,
                m.vectors.c.recipe == "chunk-v1",
            )
        ).mappings()
    }


def candidates(session: Session, caller: Caller, request, query_vector, model, *, items=None):
    session.execute(text("set local hnsw.iterative_scan = 'strict_order'"))
    latest = (
        select(m.doc_versions.c.item, func.max(m.doc_versions.c.version).label("version"))
        .group_by(m.doc_versions.c.item)
        .subquery()
    )
    base = m.provider_items.join(
        m.doc_versions, m.provider_items.c.item == m.doc_versions.c.item
    ).join(
        latest,
        and_(m.doc_versions.c.item == latest.c.item, m.doc_versions.c.version == latest.c.version),
    )
    filters = [readable(caller), m.provider_items.c.deleted_at.is_(None)]
    if items is not None:
        filters.append(m.provider_items.c.item.in_(items))
    for field, values in [
        ("mission_key", request.missions),
        ("silo", request.silos),
        ("stage", request.stages),
    ]:
        if values is not None:
            filters.append(m.provider_items.c[field].in_(values))
    ts_query = func.websearch_to_tsquery("english", request.query)
    common = [m.provider_items, m.doc_versions.c.id.label("version_id"), m.doc_versions.c.version]
    result = []
    for kind in ["chunk", "document"]:
        if request.kinds is not None and kind not in request.kinds:
            continue
        if kind == "chunk":
            source = base.join(m.chunks, m.chunks.c.doc_version_id == m.doc_versions.c.id)
            fields = [*common, m.chunks]
            tsv = m.chunks.c.tsv
            owner = m.chunks.c.id
        else:
            source = base
            fields = [*common, m.doc_versions.c.summary.label("text")]
            tsv = m.doc_versions.c.summary_tsv
            owner = m.doc_versions.c.id
        lexical = (
            select(*fields, func.ts_rank_cd(tsv, ts_query).label("raw_score"))
            .select_from(source)
            .where(*filters, tsv.op("@@")(ts_query))
            .order_by(func.ts_rank_cd(tsv, ts_query).desc())
            .limit(50)
        )
        vector_kind = "chunk" if kind == "chunk" else "doc_version"
        source = source.join(
            m.vectors, and_(m.vectors.c.owner_kind == vector_kind, m.vectors.c.owner_id == owner)
        )
        distance = m.vectors.c.embedding.cosine_distance(query_vector)
        semantic = (
            select(*fields, (1 - distance).label("raw_score"))
            .select_from(source)
            .where(*filters, m.vectors.c.model == model)
            .order_by(distance)
            .limit(50)
        )
        for method, statement in [("lexical", lexical), ("semantic", semantic)]:
            result.extend(
                (kind, method, dict(row)) for row in session.execute(statement).mappings()
            )
    # Each method has one global candidate budget across summaries and chunks.
    selected = []
    for method in ["lexical", "semantic"]:
        pool = [candidate for candidate in result if candidate[1] == method]
        pool.sort(
            key=lambda candidate: (
                -candidate[2]["raw_score"],
                candidate[2]["item"],
                candidate[2].get("block", 0),
            )
        )
        selected.extend(pool[:50])
    return selected


document_revisions = m.document_revisions
REVISION_COLUMNS = [c for c in document_revisions.c if c.name != "bytes"]


def add_revision(session: Session, values: dict) -> dict:
    """The next revision number for the item, under a per-item lock."""
    session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": "revision:" + values["item"]},
    )
    number = session.execute(
        select(func.coalesce(func.max(document_revisions.c.revision), 0) + 1).where(
            document_revisions.c.item == values["item"]
        )
    ).scalar_one()
    return dict(
        session.execute(
            document_revisions.insert()
            .values(values | {"revision": number})
            .returning(*REVISION_COLUMNS)
        )
        .mappings()
        .one()
    )


def revisions(session: Session, item: str) -> list[dict]:
    return [
        dict(row)
        for row in session.execute(
            select(*REVISION_COLUMNS)
            .where(document_revisions.c.item == item)
            .order_by(document_revisions.c.revision)
            .limit(500)
        ).mappings()
    ]


def revision(session: Session, item: str, number: int, *, content: bool = False) -> dict | None:
    columns = list(document_revisions.c) if content else REVISION_COLUMNS
    row = (
        session.execute(
            select(*columns).where(
                document_revisions.c.item == item, document_revisions.c.revision == number
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


# The read-only view from migration 0028; not a registered table, so Alembic leaves it alone.
document_relations = table(
    "document_relations",
    column("relation"),
    column("item"),
    column("version"),
    column("target_kind"),
    column("target"),
)


def relation_documents(session: Session, caller: Caller, items=None, mission=None):
    """Readable, not deleted documents, by item and/or mission."""
    query = select(
        m.provider_items.c.item, m.provider_items.c.title, m.provider_items.c.mission_key
    ).where(readable(caller), m.provider_items.c.deleted_at.is_(None))
    if items is not None:
        query = query.where(m.provider_items.c.item.in_(list(items)))
    if mission is not None:
        query = query.where(m.provider_items.c.mission_key == mission)
    return {row["item"]: dict(row) for row in session.execute(query).mappings()}


def relation_rows(session: Session, items=None, targets=None):
    """The view's rows for these documents, or pointing at these (target_kind, target) pairs."""
    r = document_relations.c
    query = select(document_relations).order_by(r.item, r.relation, r.version, r.target)
    if items is not None:
        query = query.where(r.item.in_(list(items)))
    if targets is not None:
        query = query.where(tuple_(r.target_kind, r.target).in_(list(targets)))
    return [dict(row) for row in session.execute(query).mappings()]


def relation_values(session: Session, caller: Caller, identifiers):
    """Labels of the cube values the caller's mission and silo grants reach, admin or not."""
    grant = (
        select(m.mission_grants.c.user_id)
        .where(
            m.mission_grants.c.user_id == caller.user_id,
            m.mission_grants.c.mission_key == m.cube_values.c.mission_key,
            or_(m.mission_grants.c.silo.is_(None), m.mission_grants.c.silo == m.cube_values.c.silo),
        )
        .exists()
    )
    query = (
        select(m.cube_values.c.id, m.parameters.c.label)
        .join(m.parameters, m.parameters.c.key == m.cube_values.c.parameter)
        .where(grant, m.cube_values.c.id.in_([UUID(value) for value in identifiers]))
    )
    return {str(row["id"]): row["label"] for row in session.execute(query).mappings()}


def relation_facets(session: Session, identifiers):
    """Facet texts by ID, with their facet key and the items they name as sources."""
    query = select(m.facet_texts.c.id, m.facet_texts.c.facet, m.facet_texts.c.sources).where(
        m.facet_texts.c.id.in_([UUID(value) for value in identifiers])
    )
    return {str(row["id"]): dict(row) for row in session.execute(query).mappings()}
