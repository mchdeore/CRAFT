"""Deterministic hash matching preserves repeated blocks and reports only differences."""

from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any


def compare(before: Mapping[int, bytes], after: Mapping[int, bytes]) -> list[dict[str, Any]]:
    old = dict(before)
    new = dict(after)
    rows = []
    for position in sorted(old.keys() & new.keys()):
        if old[position] == new[position]:
            del old[position]
            del new[position]
    matches = defaultdict(deque)
    for position, value in sorted(new.items()):
        matches[value].append(position)
    for position, value in sorted(list(old.items())):
        if matches[value]:
            target = matches[value].popleft()
            rows.append({"change": "moved", "from_block": position, "to_block": target})
            del old[position]
            del new[target]
    for position in sorted(old.keys() & new.keys()):
        rows.append({"change": "changed", "from_block": position, "to_block": position})
        del old[position]
        del new[position]
    rows.extend({"change": "removed", "from_block": p, "to_block": None} for p in old)
    rows.extend({"change": "added", "from_block": None, "to_block": p} for p in new)
    return sorted(
        rows,
        key=lambda row: (
            row["from_block"] if row["from_block"] is not None else float("inf"),
            row["to_block"] if row["to_block"] is not None else float("inf"),
            row["change"],
        ),
    )
