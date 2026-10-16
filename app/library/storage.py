"""Readable library queries use explicit sessions and never choose transactions."""

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.grants import readable


def item(session: Session, caller: Caller, key: str):
    return (
        session.execute(
            select(m.provider_items).where(m.provider_items.c.item == key, readable(caller))
        )
        .mappings()
        .first()
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


def candidates(session: Session, caller: Caller, request, query_vector, model="fake-embed@1"):
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
