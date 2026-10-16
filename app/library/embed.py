"""Deterministic signed token hashing for simulated semantic search."""

import hashlib
import math
import re

MODEL = "fake-embed@1"


def model_name(settings=None) -> str:
    return settings.model_embedding if settings and settings.model_main != "fake" else MODEL


def embed(text: str, caller=None) -> list[float]:
    if caller and caller.settings and caller.settings.model_main != "fake":
        from app.agent.handlers import EmbeddingInput
        from app.agent.handlers import embed as configured

        return configured(caller, EmbeddingInput(text=text)).vector
    vector = [0.0] * 1536
    for word in re.findall(r"\w+", text.lower()):
        digest = hashlib.sha256(word.encode()).digest()
        vector[int.from_bytes(digest[:4]) % 1536] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector
