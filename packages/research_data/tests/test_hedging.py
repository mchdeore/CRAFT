from collections.abc import MutableMapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any, cast

import pytest

from research_data.hedging import (
    Leg,
    balanced_two_way,
    compare_model_probability,
    de_vig,
    evaluate,
    implied_probs,
    partial_fill_stress,
)

NOW = datetime(2025, 1, 1, tzinfo=UTC)
OUT = ("A", "B", "void")


def leg(venue, payouts, **kw):
    values: dict[str, Any] = dict(
        venue=venue,
        market_id="event-7-market",
        rule_id="event-rule",
        currency="USD",
        source_id=venue + "-quote",
        quantity=D("1"),
        unit_cost=D("0.5"),
        payouts={k: D(v) for k, v in payouts.items()},
        fee_per_unit=D("0"),
        fixed_fee=D("0"),
        max_quantity=D("10"),
        observed_at=NOW,
    )
    values.update(kw)
    return Leg(**values)


def evaluate_legs(legs, outcomes=OUT, **kw):
    return evaluate(
        legs, outcomes, now=NOW, max_age=timedelta(minutes=5), max_skew=timedelta(seconds=30), **kw
    )


def test_exact_decimal_binary_shares_void_and_fees():
    a = leg("shares", {"A": "1", "B": "0", "void": "1"}, unit_cost=D("0.4"), fee_per_unit=D("0.01"))
    b = leg(
        "shares2", {"A": "0", "B": "1", "void": "1"}, unit_cost=D("0.5"), fee_per_unit=D("0.01")
    )
    result = evaluate_legs([a, b])
    assert result["payoffs"] == {"A": D("0.08"), "B": D("0.08"), "void": D("1.08")}
    assert result["cost"] == D("0.92")
    assert result["worst_profit"] == D("0.08")


def test_decimal_odds_bookmaker_and_exact_negative_fees():
    a = leg("book-a", {"A": "2.1", "B": "0", "void": "1"}, unit_cost=D("1"), fee_per_unit=D("0.02"))
    b = leg("book-b", {"A": "0", "B": "2.1", "void": "1"}, unit_cost=D("1"), fee_per_unit=D("0.02"))
    result = evaluate_legs([a, b])
    assert result["payoffs"]["A"] == D("0.06")
    assert result["worst_profit"] == D("-0.04")
    loser = evaluate_legs([leg("x", {"A": "1", "B": "0", "void": "1"}, unit_cost=D("1.2"))])
    assert loser["worst_profit"] == D("-1.2")


def test_explicit_matrix_supports_arbitrary_markets_and_rule_checks():
    a = leg("x", {"yes": "3", "no": "0"})
    b = leg("y", {"yes": "0", "no": "2"})
    assert evaluate_legs([a, b], ("yes", "no"))["payoffs"]["yes"] == D("2.0")
    with pytest.raises(ValueError):
        evaluate_legs([a, leg("y", {"yes": "0", "no": "2"}, rule_id="other")], ("yes", "no"))
    with pytest.raises(ValueError):
        evaluate_legs([a, leg("y", {"yes": "0", "no": "2"})], ("yes", "void"))


def test_stale_skew_and_source_identity():
    old = leg("x", {"A": "1", "B": "0", "void": "1"}, observed_at=NOW - timedelta(minutes=10))
    with pytest.raises(ValueError):
        evaluate_legs([old])
    newer = leg("y", {"A": "0", "B": "1", "void": "1"}, observed_at=NOW - timedelta(minutes=2))
    with pytest.raises(ValueError):
        evaluate_legs([leg("x", {"A": "1", "B": "0", "void": "1"}), newer])
    with pytest.raises(ValueError):
        evaluate_legs(
            [
                leg("x", {"A": "1", "B": "0", "void": "1"}),
                leg("y", {"A": "0", "B": "1", "void": "1"}, market_id="other"),
            ]
        )


def test_invalid_numbers_distributions_and_size():
    base = leg("x", {"A": "1", "B": "0", "void": "1"})
    with pytest.raises(ValueError):
        evaluate_legs([base], probabilities={"A": D("0.5"), "B": D("0.5"), "void": D("0.1")})
    for bad in (float("nan"), True, D("Infinity")):
        with pytest.raises((ValueError, TypeError)):
            evaluate_legs([leg("x", {"A": "1", "B": "0", "void": "1"}, quantity=bad)])
    with pytest.raises(ValueError):
        evaluate_legs([leg("x", {"A": "1", "B": "0", "void": "1"}, quantity=D("11"))])


def test_partial_fill_exposes_unfilled_leg_loss_and_source_is_explicit():
    a = leg("a", {"A": "2.1", "B": "0", "void": "1"}, unit_cost=D("1"))
    b = leg("b", {"A": "0", "B": "2.1", "void": "1"}, unit_cost=D("1"))
    cases = partial_fill_stress(
        [a, b], OUT, now=NOW, max_age=timedelta(minutes=5), max_skew=timedelta(seconds=30)
    )
    assert cases["all_filled"]["worst_profit"] == D("0")
    assert cases["only_leg_0"]["worst_profit"] == D("-1")
    assert cases["neither"]["worst_profit"] == 0


def test_stakes_round_down_and_model_pricing():
    stakes = balanced_two_way(D("2"), D("2"), D("10"), D("0.01"))
    assert stakes == {
        "stake_a": D("5.00"),
        "stake_b": D("5.00"),
        "remaining_cash": D("0.00"),
        "worst_profit": D("0.00"),
    }
    assert implied_probs([D("2"), D("4")]) == [D("0.5"), D("0.25")]
    assert sum(de_vig([D("2"), D("4")])) == D("1")
    assert abs(de_vig([D("2"), D("4")])[0] - D("2") / D("3")) < D("1e-27")
    assert compare_model_probability(D("2"), D("0.6"))["expected_profit_per_unit"] == D("0.2")


def test_negative_terminal_cash_flow_can_model_scenario_futures_exposure():
    source = leg("future", {"A": "-2", "B": "3", "void": "0"}, unit_cost=D("0"))
    result = evaluate_legs([source])
    assert result["worst_profit"] == D("-2")
    assert result["source_ids"] == ["future-quote"]


def test_fractional_fills_and_large_exact_amounts():
    a = leg("a", {"A": "2.1", "B": "0", "void": "1"}, unit_cost=D("1"))
    b = leg("b", {"A": "0", "B": "2.1", "void": "1"}, unit_cost=D("1"))
    result = evaluate_legs([a, b], fills=(D("0.5"), D("1")))
    assert result["cost"] == D("1.5") and result["payoffs"]["A"] == D("-0.45")
    quantity = D("123456789012345678901234567890.123")
    value = leg(
        "large",
        {"A": "2", "B": "2", "void": "2"},
        quantity=quantity,
        max_quantity=quantity,
        unit_cost=D("1"),
    )
    assert evaluate_legs([value])["worst_profit"] == quantity


def test_all_bounded_fill_subsets_are_checked():
    legs = [leg(str(i), {"A": "1", "B": "0", "void": "1"}) for i in range(3)]
    cases = partial_fill_stress(
        legs, OUT, now=NOW, max_age=timedelta(seconds=1), max_skew=timedelta(seconds=1)
    )
    assert len(cases) == 8


def test_invalid_leg_and_time_parameter_types_have_clear_errors():
    with pytest.raises(TypeError, match="Leg"):
        evaluate_legs(["not a leg"])
    base = leg("x", {"A": "1", "B": "0", "void": "1"})
    with pytest.raises(ValueError):
        evaluate([base], OUT, now=NOW, max_age="five minutes", max_skew=timedelta(seconds=1))


def test_quote_matrix_is_an_immutable_snapshot():
    matrix = {"A": D("1"), "B": D("0"), "void": D("1")}
    from dataclasses import replace

    base = replace(leg("x", {}), payouts=matrix)
    matrix["A"] = D("100")
    assert base.payouts["A"] == D("1")
    with pytest.raises(TypeError):
        cast(MutableMapping[str, D], base.payouts)["A"] = D("2")


def test_long_fractional_fill_does_not_depend_on_global_decimal_precision():
    from decimal import localcontext

    fill = D("0.12345678901234567890123456789012345678901234567890123456789")
    base = leg("x", {"A": "2", "B": "2", "void": "2"}, unit_cost=D("1"))
    with localcontext() as context:
        context.prec = 6
        result = evaluate_legs([base], fills=[fill])
    assert result["worst_profit"] == fill


def test_normalized_multiway_probabilities_feed_the_evaluator_exactly():
    probs = de_vig([D("3.2"), D("4.6"), D("7.1")])
    base = leg("x", {"A": "1", "B": "0", "void": "1"})
    result = evaluate_legs([base], probabilities=dict(zip(OUT, probs, strict=True)))
    assert result["expected_value"] is not None
    assert result["probability_origin"] == "caller_supplied"
