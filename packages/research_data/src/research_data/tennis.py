"""Leakage-conscious, outcome-only tennis Elo baseline (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any


def _date(value: Any) -> date:
    text = str(value)
    if not re.fullmatch(r"\d{8}", text):
        raise ValueError("tourney_date must be YYYYMMDD")
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError as exc:
        raise ValueError("invalid tourney_date") from exc


def _metrics(probabilities: list[float], labels: list[int]) -> dict[str, Any]:
    if not labels:
        return {"n": 0, "brier": None, "logloss": None, "calibration": []}
    eps = 1e-15
    bins = [{"n": 0, "mean_probability": 0.0, "win_rate": 0.0} for _ in range(10)]
    for p, y in zip(probabilities, labels, strict=True):
        b = min(9, int(p * 10))
        bins[b]["n"] += 1
        bins[b]["mean_probability"] += p
        bins[b]["win_rate"] += y
    calibration = []
    for bin_index, item in enumerate(bins):
        n = item["n"]
        if n:
            calibration.append(
                {
                    "bin": bin_index,
                    "n": n,
                    "mean_probability": item["mean_probability"] / n,
                    "win_rate": item["win_rate"] / n,
                }
            )
    return {
        "n": len(labels),
        "brier": sum((p - y) ** 2 for p, y in zip(probabilities, labels, strict=True))
        / len(labels),
        "logloss": -sum(
            y * math.log(max(eps, p)) + (1 - y) * math.log(max(eps, 1 - p))
            for p, y in zip(probabilities, labels, strict=True)
        )
        / len(labels),
        "calibration": calibration,
    }


def evaluate(
    rows: Iterable[Mapping[str, Any]],
    holdout_start: str,
    availability_days: int = 21,
    k: float = 20,
) -> dict[str, Any]:
    """Evaluate held-out match outcomes using ratings learned only before the split.

    Match dates are tournament start dates; no within-tournament chronology is
    inferred. Results become eligible only after the configured availability gap.
    """
    cutoff = _date(holdout_start)
    if type(availability_days) is not int or availability_days < 0:
        raise ValueError("availability_days must be a nonnegative integer")
    if (
        isinstance(k, bool)
        or not isinstance(k, (int, float))
        or not math.isfinite(k)
        or not 0 <= k <= 200
    ):
        raise ValueError("k must be finite and between zero and 200")
    gap = timedelta(days=availability_days)
    valid: list[tuple[date, str, str, int, str]] = []
    dropped: dict[str, int] = defaultdict(int)
    tournament_dates: dict[str, date] = {}
    seen: set[tuple[Any, ...]] = set()
    identity_rows: list[tuple[str, ...]] = []
    for row in rows:
        try:
            tid = str(row["tourney_id"]).strip()
            d = _date(row["tourney_date"])
        except (KeyError, TypeError, ValueError):
            dropped["missing_or_malformed"] += 1
            continue
        if tid in tournament_dates and tournament_dates[tid] != d:
            raise ValueError(f"ambiguous start date for tournament {tid}")
        tournament_dates[tid] = d
        winner_raw, loser_raw = row.get("winner_id"), row.get("loser_id")
        if (
            winner_raw is None
            or loser_raw is None
            or isinstance(winner_raw, bool)
            or isinstance(loser_raw, bool)
        ):
            dropped["missing_or_malformed"] += 1
            continue
        winner, loser = str(winner_raw).strip(), str(loser_raw).strip()
        score = str(row.get("score", "")).strip()
        if re.search(r"\b(ret|retired|w/o|walkover|walkover|abd)\b", score, re.I):
            dropped["retirement_or_walkover"] += 1
            continue
        if not winner or not loser or not score:
            dropped["missing_or_malformed"] += 1
            continue
        if winner == loser:
            dropped["self_match"] += 1
            continue
        # Stable match identity, independent of winner-column orientation.
        key = (
            tid,
            d.isoformat(),
            str(row.get("match_num", "")),
            min(winner, loser),
            max(winner, loser),
            score,
        )
        if key in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(key)
        a, b = sorted((winner, loser))
        label = int(winner == a)
        valid.append((d, a, b, label, tid))
        identity_rows.append((*key, str(label)))

    # Hash normalized outcome inputs, not irrelevant post-match columns or row order.
    payload = json.dumps(sorted(identity_rows), separators=(",", ":"), ensure_ascii=True)
    identity = hashlib.sha256(payload.encode()).hexdigest()
    valid.sort()
    train = [m for m in valid if m[0] < cutoff and m[0] + gap <= cutoff]
    test = [m for m in valid if m[0] >= cutoff]
    ratings: dict[str, float] = defaultdict(lambda: 1500.0)
    for day in sorted({m[0] for m in train}):
        batch = [m for m in train if m[0] == day]
        delta: dict[str, float] = defaultdict(float)
        for _, a, b, label, _ in batch:
            ra, rb = ratings[a], ratings[b]
            p_a = 1 / (1 + 10 ** ((rb - ra) / 400))
            change = k * (label - p_a)
            delta[a] += change
            delta[b] -= change
        for player, change in delta.items():
            ratings[player] += change

    probabilities, labels = [], []
    for _, a, b, label, _ in test:
        probabilities.append(
            1 / (1 + 10 ** ((ratings.get(b, 1500.0) - ratings.get(a, 1500.0)) / 400))
        )
        labels.append(label)
    players = {p for _, a, b, _, _ in test for p in (a, b)}
    covered = sum(a in ratings and b in ratings for _, a, b, _, _ in test)
    return {
        "split": {
            "holdout_start": cutoff.strftime("%Y%m%d"),
            "availability_days": availability_days,
            "latest_eligible_training_start": (cutoff - gap).strftime("%Y%m%d"),
            "date_semantics": "tournament_start_with_conservative_availability",
        },
        "input_sha256": identity,
        "counts": {
            "train": len(train),
            "test": len(test),
            "embargoed": len(valid) - len(train) - len(test),
            "dropped": dict(sorted(dropped.items())),
        },
        "coverage": {
            "test_players": len(players),
            "unseen_players": sum(p not in ratings for p in players),
            "fully_seen_test_matches": covered,
            "fully_seen_fraction": covered / len(test) if test else 0.0,
        },
        "heldout": {
            "neutral": _metrics([0.5] * len(labels), labels),
            "elo": _metrics(probabilities, labels),
        },
    }
