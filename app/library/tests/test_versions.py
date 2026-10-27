"""Real provider bytes, persisted differences, row evidence and readable version checks."""

import hashlib
from collections import Counter

from app.library.diff import compare


def hashes(values):
    return {index: hashlib.sha256(value.encode()).digest() for index, value in enumerate(values, 1)}


def markdown(values):
    return "\n\n".join(
        f"# {index} Section\n\nSimulated block {value}." for index, value in enumerate(values, 1)
    ).encode()


def test_diff_labels():
    old = hashes(["a", "b", "c", "d", "e", "f", "g", "h"])
    new = hashes(["a", "x", "d", "c", "g", "f", "b", "z"])
    result = compare(old, new)
    assert Counter(row["change"] for row in result) == {
        "moved": 4,
        "added": 1,
        "removed": 1,
        "changed": 1,
    }
    assert compare(hashes(["repeat", "repeat", "end"]), hashes(["repeat", "end", "repeat"])) == [
        {"change": "moved", "from_block": 2, "to_block": 3},
        {"change": "moved", "from_block": 3, "to_block": 2},
    ]
    assert compare(old, old) == []


def test_examples_validate():
    import json
    from pathlib import Path

    import jsonschema

    from app.tests.test_contract import typescript_schema

    for filename, shape in [("citation-status", "CitationStatus"), ("version-diff", "VersionDiff")]:
        jsonschema.validate(
            json.loads(Path(f"app/contract/examples/{filename}.json").read_text()),
            typescript_schema(shape),
        )
