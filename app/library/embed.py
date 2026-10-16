"""Deterministic signed token hashing for simulated semantic search."""

import hashlib
import math
import re

MODEL = "fake-embed@1"


def embed(text: str) -> list[float]:
    vector = [0.0] * 1536
    for word in re.findall(r"\w+", text.lower()):
        digest = hashlib.sha256(word.encode()).digest()
        vector[int.from_bytes(digest[:4]) % 1536] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector
