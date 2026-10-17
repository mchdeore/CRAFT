"""Typed structural, evidence, and similarity-candidate relationships."""

from __future__ import annotations

import math
import sqlite3
import uuid
from typing import Any

from .embeddings import get_embedding
from .models import RelationInfo
from .store import DocumentStore
from .timeutil import dump_datetime


def add_structural_edge(
    store: DocumentStore,
    *,
    source_version_id: str,
    target_version_id: str,
    relation: str,
) -> RelationInfo:
    _required(relation, "relation")
    edge_id = str(uuid.uuid4())
    with store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO structural_edges (
                edge_id, source_version_id, target_version_id, relation, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                edge_id,
                source_version_id,
                target_version_id,
                relation,
                dump_datetime(store.clock()),
            ),
        )
    return RelationInfo(
        edge_id=edge_id,
        edge_type="structural",
        source_version_id=source_version_id,
        target_version_id=target_version_id,
        relation=relation,
    )


def add_evidence_edge(
    store: DocumentStore,
    *,
    source_version_id: str,
    source_block_id: str,
    target_version_id: str,
    relation: str,
    evidence_hash: str | None = None,
) -> RelationInfo:
    _required(relation, "relation")
    with store.read_connection() as connection:
        block = connection.execute(
            "SELECT text_hash FROM blocks WHERE version_id = ? AND block_id = ?",
            (source_version_id, source_block_id),
        ).fetchone()
    if block is None:
        raise ValueError("evidence block does not belong to source version")
    if evidence_hash is not None and evidence_hash != block["text_hash"]:
        raise ValueError("evidence hash does not match the source block")
    edge_id = str(uuid.uuid4())
    with store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO evidence_edges (
                edge_id, source_version_id, source_block_id, target_version_id,
                relation, evidence_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge_id,
                source_version_id,
                source_block_id,
                target_version_id,
                relation,
                block["text_hash"],
                dump_datetime(store.clock()),
            ),
        )
    return RelationInfo(
        edge_id=edge_id,
        edge_type="evidence",
        source_version_id=source_version_id,
        target_version_id=target_version_id,
        relation=relation,
        source_block_id=source_block_id,
    )


def add_similarity_candidate(
    store: DocumentStore,
    *,
    source_embedding_id: str,
    target_embedding_id: str,
    score: float,
) -> RelationInfo:
    if not math.isfinite(score) or score < -1.0 or score > 1.0:
        raise ValueError("similarity score must be finite and in [-1, 1]")
    source_embedding = get_embedding(store, source_embedding_id)
    target_embedding = get_embedding(store, target_embedding_id)
    contract = (
        source_embedding.model,
        source_embedding.dimensions,
        source_embedding.recipe,
    )
    other_contract = (
        target_embedding.model,
        target_embedding.dimensions,
        target_embedding.recipe,
    )
    if contract != other_contract:
        raise ValueError("similarity candidates require the same model space and recipe")
    with store.read_connection() as connection:
        source = connection.execute(
            """
            SELECT version_id, representation_id FROM representations
            WHERE representation_id = ?
            """,
            (source_embedding.representation_id,),
        ).fetchone()
        target = connection.execute(
            """
            SELECT version_id, representation_id FROM representations
            WHERE representation_id = ?
            """,
            (target_embedding.representation_id,),
        ).fetchone()
    if source is None or target is None:
        raise KeyError("representation for embedding is missing")
    edge_id = str(uuid.uuid4())
    with store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO similarity_candidates (
                edge_id, source_version_id, source_representation_id,
                source_embedding_id, target_version_id, target_representation_id,
                target_embedding_id, model, dimensions, recipe, score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge_id,
                source["version_id"],
                source["representation_id"],
                source_embedding_id,
                target["version_id"],
                target["representation_id"],
                target_embedding_id,
                source_embedding.model,
                source_embedding.dimensions,
                source_embedding.recipe,
                score,
                dump_datetime(store.clock()),
            ),
        )
    return RelationInfo(
        edge_id=edge_id,
        edge_type="similarity_candidate",
        source_version_id=source["version_id"],
        target_version_id=target["version_id"],
        relation="similarity_candidate",
        model=source_embedding.model,
        recipe=source_embedding.recipe,
        score=score,
    )


def list_relations(store: DocumentStore, *, principal: str) -> tuple[RelationInfo, ...]:
    """Return edges only when both endpoint versions are readable."""
    with store.read_connection() as connection:
        rows = connection.execute(
            """
            WITH permitted_versions AS (
                SELECT v.version_id
                FROM doc_versions v
                JOIN documents d ON d.document_id = v.document_id
                JOIN mission_grants g ON g.mission = d.mission
                WHERE g.principal = ? AND g.can_read = 1
            ), all_edges AS (
                SELECT edge_id, 'structural' AS edge_type, source_version_id,
                    target_version_id, relation, NULL AS source_block_id,
                    NULL AS model, NULL AS recipe, NULL AS score
                FROM structural_edges
                UNION ALL
                SELECT edge_id, 'evidence', source_version_id, target_version_id,
                    relation, source_block_id, NULL, NULL, NULL
                FROM evidence_edges
                UNION ALL
                SELECT edge_id, 'similarity_candidate', source_version_id,
                    target_version_id, 'similarity_candidate', NULL, model, recipe, score
                FROM similarity_candidates
            )
            SELECT e.* FROM all_edges e
            JOIN permitted_versions s ON s.version_id = e.source_version_id
            JOIN permitted_versions t ON t.version_id = e.target_version_id
            ORDER BY e.edge_type, e.source_version_id, e.target_version_id, e.edge_id
            """,
            (principal,),
        ).fetchall()
    return tuple(
        RelationInfo(
            edge_id=row["edge_id"],
            edge_type=row["edge_type"],
            source_version_id=row["source_version_id"],
            target_version_id=row["target_version_id"],
            relation=row["relation"],
            source_block_id=row["source_block_id"],
            model=row["model"],
            recipe=row["recipe"],
            score=row["score"],
        )
        for row in rows
    )


def export_graph(store: DocumentStore, *, principal: str) -> dict[str, Any]:
    """Export a stable-ID, permission-filtered graph with edge provenance."""
    relations = list_relations(store, principal=principal)
    with store.read_connection() as connection:
        nodes = connection.execute(
            """
            SELECT v.version_id, v.source_key AS version_key, v.provider_version,
                v.content_hash, v.sequence, d.provider, d.remote_id, d.document_id,
                d.name, d.mission, d.classification
            FROM doc_versions v
            JOIN documents d ON d.document_id = v.document_id
            JOIN mission_grants g ON g.mission = d.mission
            WHERE g.principal = ? AND g.can_read = 1
            ORDER BY v.source_key
            """,
            (principal,),
        ).fetchall()
        node_keys = {row["version_id"]: row["version_key"] for row in nodes}
        relation_details = _relation_details(connection, relations)
    return {
        "legend": {
            "structural": "declared document/version structure",
            "evidence": "source-supported relation with block hash and location",
            "similarity_candidate": "model-scored suggestion; not a factual assertion",
        },
        "nodes": [
            {
                "id": row["version_key"],
                "kind": "document_version",
                "provider": row["provider"],
                "item": row["remote_id"] or row["document_id"],
                "version": row["provider_version"]
                or f"sha256:{row['content_hash']}#{row['sequence']}",
                "name": row["name"],
                "mission": row["mission"],
                "classification": row["classification"],
            }
            for row in nodes
        ],
        "edges": [
            {
                "id": relation.edge_id,
                "type": relation.edge_type,
                "source": node_keys[relation.source_version_id],
                "target": node_keys[relation.target_version_id],
                **relation_details.get(relation.edge_id, {}),
            }
            for relation in relations
        ],
    }


def _relation_details(
    connection: sqlite3.Connection, relations: tuple[RelationInfo, ...]
) -> dict[str, dict[str, Any]]:
    details: dict[str, dict[str, Any]] = {}
    for relation in relations:
        if relation.edge_type == "structural":
            row = connection.execute(
                "SELECT relation FROM structural_edges WHERE edge_id = ?",
                (relation.edge_id,),
            ).fetchone()
            details[relation.edge_id] = {"relation": row["relation"]}
        elif relation.edge_type == "evidence":
            row = connection.execute(
                """
                SELECT e.relation, e.evidence_hash, b.location, b.source_key
                FROM evidence_edges e JOIN blocks b ON b.block_id = e.source_block_id
                WHERE e.edge_id = ?
                """,
                (relation.edge_id,),
            ).fetchone()
            details[relation.edge_id] = {
                "relation": row["relation"],
                "evidence_hash": row["evidence_hash"],
                "location": row["location"],
                "block_source_key": row["source_key"],
            }
        else:
            row = connection.execute(
                """
                SELECT s.model, s.dimensions, s.recipe, s.score,
                    sr.source_key AS source_representation_key,
                    tr.source_key AS target_representation_key,
                    se.source_key AS source_embedding_key,
                    te.source_key AS target_embedding_key
                FROM similarity_candidates s
                JOIN representations sr ON sr.representation_id = s.source_representation_id
                JOIN representations tr ON tr.representation_id = s.target_representation_id
                JOIN embeddings se ON se.embedding_id = s.source_embedding_id
                JOIN embeddings te ON te.embedding_id = s.target_embedding_id
                WHERE s.edge_id = ?
                """,
                (relation.edge_id,),
            ).fetchone()
            details[relation.edge_id] = dict(row)
    return details


def _required(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
