"""Deterministic keyword retrieval with permission filtering inside the query."""

from __future__ import annotations

import re

from .models import Citation, SearchHit, SearchPage, SearchQuery
from .store import DocumentStore
from .timeutil import dump_datetime


def search(store: DocumentStore, query: SearchQuery) -> SearchPage:
    if not query.text.strip():
        raise ValueError("search text must not be empty")
    if query.limit < 1 or query.limit > 500:
        raise ValueError("limit must be between 1 and 500")
    if not query.principal.strip():
        raise ValueError("principal must not be empty")
    tokens = tuple(dict.fromkeys(re.findall(r"[\w.-]+", query.text.casefold())))
    if not tokens:
        return SearchPage(total_hits=0, hits=())
    kinds = tuple(kind.value for kind in query.kinds)
    if not kinds:
        return SearchPage(total_hits=0, hits=())

    score_parts = []
    token_parameters: list[str] = []
    for token in tokens[:20]:
        escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        score_parts.append("CASE WHEN lower(text) LIKE ? ESCAPE '\\' THEN 1 ELSE 0 END")
        token_parameters.append(f"%{escaped}%")
    kinds_placeholders = ", ".join("?" for _ in kinds)
    score_expression = " + ".join(score_parts)
    known_filter = "" if query.known_at is None else "AND v.created_at <= ?"
    parameters: list[object] = [query.principal, *kinds]
    if query.known_at is not None:
        parameters.append(dump_datetime(query.known_at))
    parameters.extend(token_parameters)
    parameters.append(query.limit)

    with store.read_connection() as connection:
        rows = connection.execute(
            f"""
            WITH permitted AS (
                SELECT r.representation_id, r.text, r.source_key AS representation_key,
                    r.block_id, d.document_id, d.provider, d.remote_id, d.name,
                    d.mission, v.provider_version, v.sequence, v.content_hash,
                    v.source_key AS version_key, b.location, b.source_key AS block_key
                FROM representations r
                JOIN doc_versions v ON v.version_id = r.version_id
                JOIN documents d ON d.document_id = v.document_id
                JOIN extractions x ON x.extraction_id = r.extraction_id AND x.is_current = 1
                JOIN mission_grants g ON g.mission = d.mission
                LEFT JOIN blocks b ON b.version_id = r.version_id AND b.block_id = r.block_id
                WHERE g.principal = ? AND g.can_read = 1
                  AND d.remote_status = 'active'
                  AND r.kind IN ({kinds_placeholders})
                  {known_filter}
            ), scored AS (
                SELECT permitted.*,
                    ({score_expression}) AS score
                FROM permitted
            ), matched AS (
                SELECT * FROM scored WHERE score > 0
            ), ranked AS (
                SELECT matched.*,
                    COUNT(*) OVER () AS visible_total,
                    ROW_NUMBER() OVER (
                        ORDER BY score DESC, representation_key
                    ) AS result_rank
                FROM matched
            )
            SELECT * FROM ranked WHERE result_rank <= ? ORDER BY result_rank
            """,
            parameters,
        ).fetchall()
    total = (
        rows[0]["visible_total"]
        if rows
        else _visible_match_count(
            store, query, kinds, token_parameters, score_expression, known_filter, parameters
        )
    )
    hits = tuple(
        SearchHit(
            representation_id=row["representation_id"],
            document_id=row["document_id"],
            name=row["name"],
            mission=row["mission"],
            score=row["score"],
            excerpt=row["text"],
            citation=Citation(
                provider=row["provider"],
                item_id=row["remote_id"] or row["document_id"],
                version=row["provider_version"]
                or f"sha256:{row['content_hash']}#{row['sequence']}",
                block_id=row["block_id"],
                location=row["location"],
                content_hash=row["content_hash"],
                source_key=row["block_key"] or row["representation_key"] or row["version_key"],
            ),
        )
        for row in rows
    )
    return SearchPage(total_hits=total, hits=hits)


def _visible_match_count(
    store: DocumentStore,
    query: SearchQuery,
    kinds: tuple[str, ...],
    token_parameters: list[str],
    score_expression: str,
    known_filter: str,
    parameters: list[object],
) -> int:
    count_parameters = parameters[:-1]
    with store.read_connection() as connection:
        row = connection.execute(
            f"""
            WITH permitted AS (
                SELECT r.text
                FROM representations r
                JOIN doc_versions v ON v.version_id = r.version_id
                JOIN documents d ON d.document_id = v.document_id
                JOIN extractions x ON x.extraction_id = r.extraction_id AND x.is_current = 1
                JOIN mission_grants g ON g.mission = d.mission
                WHERE g.principal = ? AND g.can_read = 1
                  AND d.remote_status = 'active'
                  AND r.kind IN ({", ".join("?" for _ in kinds)})
                  {known_filter}
            ), scored AS (
                SELECT ({score_expression}) AS score FROM permitted
            )
            SELECT COUNT(*) FROM scored WHERE score > 0
            """,
            count_parameters,
        ).fetchone()
    return int(row[0])
