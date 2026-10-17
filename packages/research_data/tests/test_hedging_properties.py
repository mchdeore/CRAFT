from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext
from fractions import Fraction
from random import Random

import pytest

from research_data.hedging import Leg, evaluate


def _f(x: Decimal) -> Fraction:
    return Fraction(str(x))


def _oracle_net(
    legs: tuple[Leg, ...], outcomes: tuple[str, ...], fills: tuple[Decimal, ...]
) -> dict[str, Fraction]:
    cost = Fraction(0)
    gross = {o: Fraction(0) for o in outcomes}
    for leg, fill in zip(legs, fills, strict=True):
        q = _f(leg.quantity) * _f(fill)
        cost += q * (_f(leg.unit_cost) + _f(leg.fee_per_unit))
        if q != 0:
            cost += _f(leg.fixed_fee)
        for o in outcomes:
            gross[o] += q * _f(leg.payouts[o])
    return {o: gross[o] - cost for o in outcomes}


def _mk_leg(rng: Random, i: int, now: datetime, outcomes: tuple[str, ...]) -> Leg:
    payouts: dict[str, Decimal] = {
        outcomes[0]: Decimal(rng.randint(-4, 6)),
        outcomes[1]: Decimal(rng.randint(-4, 6)),
    }
    return Leg(
        venue="venue",
        market_id="mkt",
        rule_id="rule",
        currency="USD",
        source_id=f"src{i}",
        quantity=Decimal(rng.randint(1, 6)),
        unit_cost=Decimal(rng.randint(1, 4)) / Decimal(10),
        payouts=payouts,
        fee_per_unit=Decimal(rng.randint(1, 3)) / Decimal(100),
        fixed_fee=Decimal(rng.randint(1, 4)) / Decimal(100),
        max_quantity=Decimal(10),
        observed_at=now - timedelta(seconds=rng.randint(0, 90)),
    )


def test_fraction_oracle_payoffs_and_precision_invariance() -> None:
    rng = Random(17)
    outcomes = ("A", "B")
    now = datetime(2026, 1, 1, tzinfo=UTC)

    for _ in range(12):
        n = rng.randint(2, 4)
        legs = tuple(_mk_leg(rng, i, now, outcomes) for i in range(n))
        fills = [
            Decimal(rng.choice([0, 1])) if rng.random() < 0.4 else Decimal("0.5") for _ in range(n)
        ]
        fills[0] = Decimal(1)
        fills_t = tuple(fills)

        result = evaluate(
            legs,
            outcomes,
            fills=fills_t,
            now=now,
            max_age=timedelta(minutes=10),
            max_skew=timedelta(minutes=5),
        )
        expected = _oracle_net(legs, outcomes, fills_t)
        for o in outcomes:
            assert Fraction(str(result["payoffs"][o])) == expected[o]

        old_prec = getcontext().prec
        try:
            getcontext().prec = 7
            low_prec = evaluate(
                legs,
                outcomes,
                fills=fills_t,
                now=now,
                max_age=timedelta(minutes=10),
                max_skew=timedelta(minutes=5),
            )
        finally:
            getcontext().prec = old_prec
        for o in outcomes:
            assert low_prec["payoffs"][o] == result["payoffs"][o]


def test_identity_mismatch_and_stale_quotes_refuse() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    outcomes = ("A", "B")
    base = Leg(
        venue="v",
        market_id="m1",
        rule_id="r1",
        currency="USD",
        source_id="s1",
        quantity=Decimal("2"),
        unit_cost=Decimal("0.20"),
        payouts={"A": Decimal("1"), "B": Decimal("-1")},
        fee_per_unit=Decimal("0.01"),
        fixed_fee=Decimal("0.02"),
        max_quantity=Decimal("3"),
        observed_at=now,
    )
    mismatch = Leg(**{**base.__dict__, "currency": "EUR", "source_id": "s2"})
    with pytest.raises(ValueError):
        evaluate(
            (base, mismatch),
            outcomes,
            now=now,
            max_age=timedelta(minutes=1),
            max_skew=timedelta(seconds=30),
        )

    stale = Leg(**{**base.__dict__, "source_id": "s3", "observed_at": now - timedelta(hours=2)})
    with pytest.raises(ValueError):
        evaluate(
            (base, stale),
            outcomes,
            now=now,
            max_age=timedelta(minutes=1),
            max_skew=timedelta(minutes=5),
        )


def test_stake_floor_uses_exact_rationals_and_never_spends_residual_cash():
    from research_data.hedging import balanced_two_way

    rng = Random(71)
    for _ in range(20):
        a = Decimal(rng.randint(101, 1000)) / 100
        b = Decimal(rng.randint(101, 1000)) / 100
        funds = Decimal(rng.randint(1, 10000)) / 100
        step = Decimal("0.01")
        result = balanced_two_way(a, b, funds, step)
        allocation = Fraction(funds) * Fraction(b) / (Fraction(a) + Fraction(b)) / Fraction(step)
        exact_floor = allocation.numerator // allocation.denominator
        assert result["stake_a"] == Decimal(exact_floor) * step
        assert result["stake_a"] + result["stake_b"] <= funds
        assert result["remaining_cash"] >= 0
