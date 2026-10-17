"""Typed factual values, temporal selection, units, and fake approval batches."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext

from .models import (
    ApprovalDecision,
    CandidateBatch,
    CandidateValueInput,
    Citation,
    CubeQuery,
    CubeQueryResult,
    CubeValueInput,
    CubeValueResult,
    PublicationResult,
    ValueState,
)
from .store import DocumentStore
from .timeutil import dump_datetime, load_datetime


@dataclass(frozen=True)
class UnitDefinition:
    dimension: str
    factor: Decimal
    offset: Decimal = Decimal(0)


UNITS: dict[str, UnitDefinition] = {
    "g": UnitDefinition("mass", Decimal("1")),
    "kg": UnitDefinition("mass", Decimal("1000")),
    "mg": UnitDefinition("mass", Decimal("0.001")),
    "lb": UnitDefinition("mass", Decimal("453.59237")),
    "W": UnitDefinition("power", Decimal("1")),
    "kW": UnitDefinition("power", Decimal("1000")),
    "MW": UnitDefinition("power", Decimal("1000000")),
    "Wh": UnitDefinition("energy", Decimal("1")),
    "kWh": UnitDefinition("energy", Decimal("1000")),
    "MWh": UnitDefinition("energy", Decimal("1000000")),
    "m": UnitDefinition("length", Decimal("1")),
    "km": UnitDefinition("length", Decimal("1000")),
    "mm": UnitDefinition("length", Decimal("0.001")),
    "K": UnitDefinition("temperature", Decimal("1")),
    "degC": UnitDefinition("temperature", Decimal("1"), Decimal("273.15")),
    "%": UnitDefinition("ratio", Decimal("0.01")),
    "ratio": UnitDefinition("ratio", Decimal("1")),
}


def insert_cube_value(store: DocumentStore, value: CubeValueInput) -> str:
    state = ValueState(value.state)
    if state is ValueState.VALUE:
        if value.amount is None or value.unit is None:
            raise ValueError("value state requires an exact amount and unit")
        _finite_decimal(value.amount)
        _unit(value.unit)
    elif value.amount is not None:
        raise ValueError("non-value states cannot carry a numeric amount")
    event_time = dump_datetime(value.event_time)
    known_at = dump_datetime(value.known_at)
    fact_id = str(uuid.uuid4())
    with store.transaction() as connection:
        _validate_source(
            connection,
            mission=value.mission,
            source_version_id=value.source_version_id,
            source_block_id=value.source_block_id,
            source_location=value.source_location,
            evidence_hash=value.evidence_hash,
        )
        connection.execute(
            """
            INSERT INTO cube_values (
                fact_id, mission, metric, subject, value_state, amount_decimal, unit,
                event_time, known_at, source_version_id, source_block_id,
                source_location, evidence_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact_id,
                value.mission,
                value.metric,
                value.subject,
                state.value,
                None if value.amount is None else str(value.amount),
                value.unit,
                event_time,
                known_at,
                value.source_version_id,
                value.source_block_id,
                value.source_location,
                value.evidence_hash,
                dump_datetime(store.clock()),
            ),
        )
    return fact_id


def query_cube(store: DocumentStore, query: CubeQuery) -> CubeQueryResult:
    at = dump_datetime(query.at)
    known_at = dump_datetime(query.known_at)
    if not query.principal.strip() or not query.mission.strip() or not query.metric.strip():
        raise ValueError("principal, mission, and metric must not be empty")
    if query.target_unit is not None:
        _unit(query.target_unit)
    subjects = tuple(sorted(set(query.subjects or ())))
    with store.read_connection() as connection:
        grant = connection.execute(
            "SELECT can_read FROM mission_grants WHERE principal = ? AND mission = ?",
            (query.principal, query.mission),
        ).fetchone()
        if grant is None or grant["can_read"] != 1:
            return CubeQueryResult(accessible=False, rows=())
        subject_clause = ""
        parameters: list[object] = [
            query.principal,
            query.mission,
            query.metric,
            at,
            known_at,
        ]
        if subjects:
            subject_clause = f"AND c.subject IN ({', '.join('?' for _ in subjects)})"
            parameters.extend(subjects)
        rows = connection.execute(
            f"""
            WITH permitted AS (
                SELECT c.*, d.provider, d.remote_id, d.document_id, d.remote_status,
                    v.sequence, v.provider_version, v.content_hash,
                    v.source_key AS version_source_key, b.location,
                    b.source_key AS block_source_key
                FROM cube_values c
                JOIN doc_versions v ON v.version_id = c.source_version_id
                JOIN documents d ON d.document_id = v.document_id AND d.mission = c.mission
                JOIN mission_grants g ON g.mission = d.mission
                JOIN blocks b ON b.version_id = c.source_version_id
                    AND b.block_id = c.source_block_id
                WHERE g.principal = ? AND g.can_read = 1
                  AND c.mission = ? AND c.metric = ?
                  AND c.event_time <= ? AND c.known_at <= ?
                  {subject_clause}
            ), ranked AS (
                SELECT permitted.*,
                    COUNT(*) OVER (PARTITION BY subject) AS visible_history_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY subject
                        ORDER BY event_time DESC, known_at DESC, fact_id
                    ) AS value_rank
                FROM permitted
            )
            SELECT * FROM ranked WHERE value_rank = 1 ORDER BY subject
            """,
            parameters,
        ).fetchall()
    result_rows = [_cube_result(row, query.target_unit) for row in rows]
    if subjects:
        actual_subjects = {row.subject for row in result_rows}
        for subject in subjects:
            if subject not in actual_subjects:
                result_rows.append(
                    CubeValueResult(
                        subject=subject,
                        mission=query.mission,
                        metric=query.metric,
                        state=ValueState.MISSING,
                        amount=None,
                        unit=query.target_unit,
                        event_time=None,
                        known_at=None,
                        source=None,
                        visible_history_count=0,
                    )
                )
    result_rows.sort(key=lambda row: row.subject)
    return CubeQueryResult(accessible=True, rows=tuple(result_rows))


def create_candidate_batch(
    store: DocumentStore, values: Iterable[CandidateValueInput]
) -> CandidateBatch:
    candidates = tuple(values)
    if not candidates:
        raise ValueError("candidate batch must contain at least one value")
    for value in candidates:
        _finite_decimal(value.amount)
        _unit(value.unit)
    canonical = [_candidate_payload(value) for value in candidates]
    canonical.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    payload_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    batch_id = str(uuid.uuid4())
    candidate_ids = tuple(str(uuid.uuid4()) for _ in candidates)
    with store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO candidate_batches (
                batch_id, payload_hash, candidate_count, created_at, published_at
            ) VALUES (?, ?, ?, ?, NULL)
            """,
            (batch_id, payload_hash, len(candidates), dump_datetime(store.clock())),
        )
        for candidate_id, value in zip(candidate_ids, candidates, strict=True):
            _validate_source(
                connection,
                mission=value.mission,
                source_version_id=value.source_version_id,
                source_block_id=value.source_block_id,
                source_location=value.source_location,
                evidence_hash=value.evidence_hash,
            )
            connection.execute(
                """
                INSERT INTO candidate_values (
                    candidate_id, batch_id, mission, metric, subject, amount_decimal,
                    unit, event_time, source_version_id, source_block_id,
                    source_location, evidence_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    batch_id,
                    value.mission,
                    value.metric,
                    value.subject,
                    str(value.amount),
                    value.unit,
                    dump_datetime(value.event_time),
                    value.source_version_id,
                    value.source_block_id,
                    value.source_location,
                    value.evidence_hash,
                ),
            )
    return CandidateBatch(batch_id, candidate_ids, payload_hash)


@dataclass(frozen=True)
class FakeApprovalBoundary:
    """Deterministic fixture boundary; it does not enforce production access."""

    issuer: str = "fake-approval-fixture"

    def decide(self, batch: CandidateBatch, *, accepted: bool) -> ApprovalDecision:
        return ApprovalDecision(
            batch_id=batch.batch_id,
            payload_hash=batch.payload_hash,
            accepted=accepted,
            issuer=self.issuer,
        )


def publish_candidate_batch(
    store: DocumentStore, batch_id: str, decision: ApprovalDecision
) -> PublicationResult:
    if not decision.accepted:
        return PublicationResult(status="rejected", fact_ids=(), inserted_count=0)
    if decision.batch_id != batch_id or not decision.issuer:
        raise ValueError("approval decision does not identify this batch")
    inserted_ids: list[str] = []
    with store.transaction() as connection:
        batch = connection.execute(
            "SELECT * FROM candidate_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if batch is None:
            raise KeyError(batch_id)
        if batch["published_at"] is not None:
            rows = connection.execute(
                """
                SELECT p.fact_id FROM candidate_publications p
                JOIN candidate_values c ON c.candidate_id = p.candidate_id
                WHERE c.batch_id = ? ORDER BY c.candidate_id
                """,
                (batch_id,),
            ).fetchall()
            return PublicationResult(
                status="replayed",
                fact_ids=tuple(row["fact_id"] for row in rows),
                inserted_count=0,
            )
        candidates = connection.execute(
            "SELECT * FROM candidate_values WHERE batch_id = ? ORDER BY candidate_id",
            (batch_id,),
        ).fetchall()
        if len(candidates) != batch["candidate_count"]:
            raise ValueError("candidate batch count changed before publication")
        actual_payload_hash = _candidate_rows_hash(candidates)
        if (
            actual_payload_hash != batch["payload_hash"]
            or decision.payload_hash != batch["payload_hash"]
        ):
            raise ValueError("approval payload does not match immutable candidates")
        known_at = dump_datetime(store.clock())
        for candidate in candidates:
            _validate_source(
                connection,
                mission=candidate["mission"],
                source_version_id=candidate["source_version_id"],
                source_block_id=candidate["source_block_id"],
                source_location=candidate["source_location"],
                evidence_hash=candidate["evidence_hash"],
            )
            fact_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO cube_values (
                    fact_id, mission, metric, subject, value_state, amount_decimal, unit,
                    event_time, known_at, source_version_id, source_block_id,
                    source_location, evidence_hash, created_at
                ) VALUES (?, ?, ?, ?, 'value', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact_id,
                    candidate["mission"],
                    candidate["metric"],
                    candidate["subject"],
                    candidate["amount_decimal"],
                    candidate["unit"],
                    candidate["event_time"],
                    known_at,
                    candidate["source_version_id"],
                    candidate["source_block_id"],
                    candidate["source_location"],
                    candidate["evidence_hash"],
                    known_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO candidate_publications(candidate_id, fact_id, published_at)
                VALUES (?, ?, ?)
                """,
                (candidate["candidate_id"], fact_id, known_at),
            )
            inserted_ids.append(fact_id)
        connection.execute(
            "UPDATE candidate_batches SET published_at = ? WHERE batch_id = ?",
            (known_at, batch_id),
        )
    return PublicationResult(
        status="published",
        fact_ids=tuple(inserted_ids),
        inserted_count=len(inserted_ids),
    )


def _validate_source(
    connection: sqlite3.Connection,
    *,
    mission: str,
    source_version_id: str,
    source_block_id: str,
    source_location: str,
    evidence_hash: str,
) -> None:
    row = connection.execute(
        """
        SELECT d.mission, d.remote_status, b.location, b.text_hash
        FROM doc_versions v JOIN documents d ON d.document_id = v.document_id
        JOIN blocks b ON b.version_id = v.version_id
        WHERE v.version_id = ? AND b.block_id = ?
        """,
        (source_version_id, source_block_id),
    ).fetchone()
    if row is None:
        raise ValueError("source block does not belong to source version")
    if row["mission"] != mission:
        raise ValueError("cube mission must match the source document mission")
    if row["remote_status"] != "active":
        raise ValueError("withdrawn provider items cannot support new cube rows")
    if row["location"] != source_location or row["text_hash"] != evidence_hash:
        raise ValueError("source location or evidence hash does not match the source block")


def _cube_result(row: sqlite3.Row, target_unit: str | None) -> CubeValueResult:
    state = ValueState(row["value_state"])
    withdrawn = row["remote_status"] != "active"
    if withdrawn:
        state = ValueState.WITHHELD
    amount = (
        Decimal(row["amount_decimal"])
        if row["amount_decimal"] is not None and not withdrawn
        else None
    )
    unit = row["unit"]
    if amount is not None and target_unit is not None:
        amount = convert_unit(amount, unit, target_unit)
        unit = target_unit
    item_id = row["remote_id"] or row["document_id"]
    version = row["provider_version"] or f"sha256:{row['content_hash']}#{row['sequence']}"
    source = Citation(
        provider=row["provider"],
        item_id=item_id,
        version=version,
        block_id=row["source_block_id"],
        location=row["source_location"],
        content_hash=row["evidence_hash"],
        source_key=row["block_source_key"],
    )
    return CubeValueResult(
        subject=row["subject"],
        mission=row["mission"],
        metric=row["metric"],
        state=state,
        amount=amount,
        unit=unit,
        event_time=load_datetime(row["event_time"]),
        known_at=load_datetime(row["known_at"]),
        source=None if withdrawn else source,
        visible_history_count=row["visible_history_count"],
        fact_id=row["fact_id"],
    )


def convert_unit(amount: Decimal, source_unit: str | None, target_unit: str) -> Decimal:
    if source_unit is None:
        raise ValueError("source unit is missing")
    source = _unit(source_unit)
    target = _unit(target_unit)
    if source.dimension != target.dimension:
        raise ValueError(f"incompatible units: {source_unit} and {target_unit}")
    amount = _finite_decimal(amount)
    with localcontext() as context:
        context.prec = max(
            50,
            len(amount.as_tuple().digits)
            + len(source.factor.as_tuple().digits)
            + len(target.factor.as_tuple().digits)
            + abs(int(amount.as_tuple().exponent))
            + 28,
        )
        base_amount = amount * source.factor + source.offset
        return (base_amount - target.offset) / target.factor


def _unit(unit: str | None) -> UnitDefinition:
    if unit is None or unit not in UNITS:
        raise ValueError(f"unknown unit: {unit}")
    return UNITS[unit]


def _finite_decimal(amount: Decimal) -> Decimal:
    try:
        value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("amount must be an exact decimal") from error
    if not value.is_finite():
        raise ValueError("amount must be finite")
    return value


def _candidate_payload(value: CandidateValueInput) -> dict[str, str]:
    return {
        "mission": value.mission,
        "metric": value.metric,
        "subject": value.subject,
        "amount": str(value.amount),
        "unit": value.unit,
        "event_time": dump_datetime(value.event_time),
        "source_version_id": value.source_version_id,
        "source_block_id": value.source_block_id,
        "source_location": value.source_location,
        "evidence_hash": value.evidence_hash,
    }


def _candidate_rows_hash(rows: list[sqlite3.Row]) -> str:
    canonical = [
        {
            "mission": row["mission"],
            "metric": row["metric"],
            "subject": row["subject"],
            "amount": row["amount_decimal"],
            "unit": row["unit"],
            "event_time": row["event_time"],
            "source_version_id": row["source_version_id"],
            "source_block_id": row["source_block_id"],
            "source_location": row["source_location"],
            "evidence_hash": row["evidence_hash"],
        }
        for row in rows
    ]
    canonical.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
