"""Fake vector plumbing and normalized representation-to-embedding storage."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass

from .store import DocumentStore
from .timeutil import dump_datetime


@dataclass(frozen=True)
class EmbeddingRecord:
    embedding_id: str
    representation_id: str
    payload_id: str
    model: str
    dimensions: int
    input_type: str
    recipe: str
    text_hash: str
    vector: tuple[float, ...]


class FakeEmbedding:
    """Deterministic finite vectors for schema and plumbing checks only."""

    model = "fake-shake-v1"
    recipe = "sha256-shake-centered-u32-v1"

    @classmethod
    def embed(cls, text: str, *, dimensions: int = 16) -> tuple[float, ...]:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        raw = hashlib.shake_256(seed).digest(dimensions * 4)
        vector = tuple(
            (int.from_bytes(raw[offset : offset + 4], "big") / 4_294_967_295.0) * 2.0 - 1.0
            for offset in range(0, len(raw), 4)
        )
        return vector


def insert_embedding(
    store: DocumentStore,
    representation_id: str,
    vector: tuple[float, ...] | list[float],
    *,
    model: str,
    dimensions: int,
    input_type: str,
    recipe: str,
) -> EmbeddingRecord:
    if dimensions <= 0 or len(vector) != dimensions:
        raise ValueError("embedding dimension must match the declared dimension")
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("embedding values must be finite")
    if not all(isinstance(value, (int, float)) for value in vector):
        raise TypeError("embedding values must be numeric")
    if not model or not input_type or not recipe:
        raise ValueError("model, input_type, and recipe must be recorded")
    packed = tuple(float(value) for value in vector)
    vector_json = json.dumps(packed, separators=(",", ":"), allow_nan=False)
    embedding_id = str(uuid.uuid4())
    with store.transaction() as connection:
        representation = connection.execute(
            "SELECT * FROM representations WHERE representation_id = ?",
            (representation_id,),
        ).fetchone()
        if representation is None:
            raise KeyError(representation_id)
        key = (model, dimensions, input_type, recipe, representation["text_hash"])
        payload = connection.execute(
            """
            SELECT * FROM embedding_payloads
            WHERE model = ? AND dimensions = ? AND input_type = ?
              AND recipe = ? AND text_hash = ?
            """,
            key,
        ).fetchone()
        if payload is None:
            payload_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO embedding_payloads (
                    payload_id, model, dimensions, input_type, recipe,
                    text_hash, vector_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload_id,
                    model,
                    dimensions,
                    input_type,
                    recipe,
                    representation["text_hash"],
                    vector_json,
                    dump_datetime(store.clock()),
                ),
            )
        else:
            payload_id = payload["payload_id"]
            if payload["vector_json"] != vector_json:
                raise ValueError("same model/recipe/text key returned a different cached vector")
        embedded = connection.execute(
            "SELECT * FROM embeddings WHERE representation_id = ? AND payload_id = ?",
            (representation_id, payload_id),
        ).fetchone()
        if embedded is None:
            embedding_contract = hashlib.sha256(
                f"{model}\x1f{dimensions}\x1f{input_type}\x1f{recipe}".encode()
            ).hexdigest()
            source_key = f"{representation['source_key']}/embedding:{embedding_contract}"
            connection.execute(
                """
                INSERT INTO embeddings (
                    embedding_id, representation_id, payload_id, source_key, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    embedding_id,
                    representation_id,
                    payload_id,
                    source_key,
                    dump_datetime(store.clock()),
                ),
            )
        else:
            embedding_id = embedded["embedding_id"]

    return EmbeddingRecord(
        embedding_id=embedding_id,
        representation_id=representation_id,
        payload_id=payload_id,
        model=model,
        dimensions=dimensions,
        input_type=input_type,
        recipe=recipe,
        text_hash=representation["text_hash"],
        vector=packed,
    )


def get_embedding(store: DocumentStore, embedding_id: str) -> EmbeddingRecord:
    with store.read_connection() as connection:
        row = connection.execute(
            """
            SELECT e.embedding_id, e.representation_id, e.payload_id,
                p.model, p.dimensions, p.input_type, p.recipe, p.text_hash, p.vector_json
            FROM embeddings e JOIN embedding_payloads p ON p.payload_id = e.payload_id
            WHERE e.embedding_id = ?
            """,
            (embedding_id,),
        ).fetchone()
    if row is None:
        raise KeyError(embedding_id)
    return EmbeddingRecord(
        embedding_id=row["embedding_id"],
        representation_id=row["representation_id"],
        payload_id=row["payload_id"],
        model=row["model"],
        dimensions=row["dimensions"],
        input_type=row["input_type"],
        recipe=row["recipe"],
        text_hash=row["text_hash"],
        vector=tuple(json.loads(row["vector_json"])),
    )


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("vectors must have the same nonzero dimension")
    dot = math.fsum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(math.fsum(a * a for a in left))
    right_norm = math.sqrt(math.fsum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
