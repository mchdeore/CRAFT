"""Exact-Decimal payoff research helpers; not execution or hedging advice."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from types import MappingProxyType


def _d(value, name="value"):
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{name} must not be bool or float")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    else:
        raise TypeError(f"{name} must be Decimal or int")
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    if len(result.as_tuple().digits) + abs(int(result.as_tuple().exponent)) > 1000:
        raise ValueError("number exceeds arithmetic size limit")
    return result


@dataclass(frozen=True)
class Leg:
    venue: str
    market_id: str
    rule_id: str
    currency: str
    source_id: str
    quantity: Decimal
    unit_cost: Decimal
    payouts: Mapping[str, Decimal]
    fee_per_unit: Decimal
    fixed_fee: Decimal
    max_quantity: Decimal
    observed_at: datetime

    def __post_init__(self):
        if not isinstance(self.payouts, Mapping):
            raise TypeError("payouts must be a mapping")
        object.__setattr__(self, "payouts", MappingProxyType(dict(self.payouts)))


def _distribution(probabilities, outcomes):
    if probabilities is None:
        return None
    if set(probabilities) != set(outcomes):
        raise ValueError("probabilities must cover exactly the outcomes")
    probs = {o: _d(probabilities[o], "probability") for o in outcomes}
    if any(p < 0 or p > 1 for p in probs.values()) or sum(probs.values()) != 1:
        raise ValueError("probabilities must be in [0,1] and sum exactly to 1")
    return probs


def _evaluate(
    legs: Sequence[Leg],
    outcomes: Sequence[str],
    probabilities=None,
    fills=None,
    *,
    now: datetime,
    max_age: timedelta,
    max_skew: timedelta,
):
    """Evaluate declared joint outcomes; identity completeness is caller supplied."""
    legs, outcomes = tuple(legs), tuple(outcomes)
    if not 1 <= len(legs) <= 64 or not 1 <= len(outcomes) <= 64:
        raise ValueError("one to 64 legs and outcomes required")
    if len(set(outcomes)) != len(outcomes):
        raise ValueError("legs and unique outcomes are required")
    if not all(isinstance(x, str) and x for x in outcomes):
        raise ValueError("outcomes must be nonempty strings")
    if (
        not isinstance(max_age, timedelta)
        or not isinstance(max_skew, timedelta)
        or not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
        or max_age < timedelta(0)
        or max_skew < timedelta(0)
    ):
        raise ValueError("aware now and nonnegative age/skew required")
    if fills is None:
        fills = (True,) * len(legs)
    fills = tuple(fills)
    if len(fills) != len(legs):
        raise ValueError("fills must contain one value per leg")
    fills = tuple(
        Decimal(int(value)) if type(value) is bool else _d(value, "fill") for value in fills
    )
    if any(value < 0 or value > 1 for value in fills):
        raise ValueError("fill fraction must be within zero and one")
    rules, currencies, markets = set(), set(), set()
    times = []
    for leg in legs:
        if not isinstance(leg, Leg):
            raise TypeError("each leg must be a Leg")
        for field in (leg.venue, leg.market_id, leg.rule_id, leg.currency, leg.source_id):
            if not isinstance(field, str) or not field:
                raise ValueError("venue, market, rule, currency and source required")
        rules.add(leg.rule_id)
        currencies.add(leg.currency)
        markets.add(leg.market_id)
        if set(leg.payouts) != set(outcomes):
            raise ValueError("each payout matrix must cover exactly the outcomes")
        q = _d(leg.quantity, "quantity")
        cap = _d(leg.max_quantity, "max_quantity")
        cost = _d(leg.unit_cost, "unit_cost")
        fee = _d(leg.fee_per_unit, "fee_per_unit")
        fixed = _d(leg.fixed_fee, "fixed_fee")
        if q < 0 or cap < 0 or q > cap or cost < 0 or fee < 0 or fixed < 0:
            raise ValueError("invalid negative amount or size limit exceeded")
        for value in leg.payouts.values():
            _d(value, "payout")
        t = leg.observed_at
        if not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None:
            raise ValueError("quote timestamps must be timezone-aware")
        age = now - t
        if age < timedelta(0) or age > max_age:
            raise ValueError("quote is stale or from the future")
        times.append(t)
    if len(rules) != 1 or len(currencies) != 1 or len(markets) != 1:
        raise ValueError("legs must share declared market, rule and currency identity")
    if max(times) - min(times) > max_skew:
        raise ValueError("quotes are too asynchronous")
    probs = _distribution(probabilities, outcomes)
    cost_total = Decimal(0)
    payoffs = {o: Decimal(0) for o in outcomes}
    for leg, filled in zip(legs, fills, strict=True):
        if filled:
            q = _d(leg.quantity) * filled
            cost_total += q * (_d(leg.unit_cost) + _d(leg.fee_per_unit))
            if q:
                cost_total += _d(leg.fixed_fee)
            for o in outcomes:
                payoffs[o] += q * _d(leg.payouts[o])
    net = {o: value - cost_total for o, value in payoffs.items()}
    return {
        "payoffs": net,
        "cost": cost_total,
        "worst_profit": min(net.values()),
        "expected_value": (sum(probs[o] * net[o] for o in outcomes) if probs is not None else None),
        "source_ids": [leg.source_id for leg in legs],
        "market_id": legs[0].market_id,
        "rule_id": legs[0].rule_id,
        "currency": legs[0].currency,
        "quote_times": [leg.observed_at.isoformat() for leg in legs],
        "probability_origin": "caller_supplied" if probs is not None else None,
        "scope": "declared outcomes and supplied fill assumptions",
    }


def evaluate(legs, outcomes, probabilities=None, fills=None, *, now, max_age, max_skew):
    legs = tuple(legs)
    if not all(isinstance(leg, Leg) for leg in legs):
        raise TypeError("each leg must be a Leg")
    if fills is not None:
        fills = tuple(fills)
    numbers = [
        value
        for leg in legs
        for value in (
            leg.quantity,
            leg.unit_cost,
            leg.fee_per_unit,
            leg.fixed_fee,
            leg.max_quantity,
            *leg.payouts.values(),
        )
    ]
    if probabilities is not None:
        if not isinstance(probabilities, Mapping):
            raise TypeError("probabilities must be a mapping")
        numbers.extend(probabilities.values())
    if fills is not None:
        numbers.extend(value for value in fills if type(value) is not bool)
    digits = max(
        (
            len(_d(value).as_tuple().digits) + abs(int(_d(value).as_tuple().exponent))
            for value in numbers
        ),
        default=1,
    )
    with localcontext() as context:
        context.prec = max(50, 5 * digits + 32)
        return _evaluate(
            legs, outcomes, probabilities, fills, now=now, max_age=max_age, max_skew=max_skew
        )


def partial_fill_stress(legs, outcomes, probabilities=None, *, now, max_age, max_skew):
    """Enumerate every full/unfilled subset; fractional fills use evaluate."""
    legs = tuple(legs)
    n = len(legs)
    if not 1 <= n <= 8:
        raise ValueError("stress enumeration requires one to eight legs")
    cases = [("all_filled", (True,) * n)]
    cases += [(f"only_leg_{i}", tuple(j == i for j in range(n))) for i in range(n)]
    cases.append(("neither", (False,) * n))
    used = {flags for _, flags in cases}
    for mask in range(1 << n):
        flags = tuple(bool(mask & (1 << index)) for index in range(n))
        if flags not in used:
            cases.append((f"subset_{mask}", flags))
    return {
        name: evaluate(
            legs, outcomes, probabilities, fills, now=now, max_age=max_age, max_skew=max_skew
        )
        for name, fills in cases
    }


def _balanced_two_way(odds_a, odds_b, budget, step=Decimal("0.01")):
    """Floor-rounded opposing decimal-odds stakes and residual cash."""
    a, b, budget, step = (_d(odds_a), _d(odds_b), _d(budget), _d(step))
    if a <= 1 or b <= 1 or budget < 0 or step <= 0:
        raise ValueError("odds must exceed one; budget/step must be valid")
    fa, fb, funds, quantum = map(Fraction, (a, b, budget, step))
    allocation_a = funds * fb / (fa + fb) / quantum
    allocation_b = funds * fa / (fa + fb) / quantum
    sa = Decimal(allocation_a.numerator // allocation_a.denominator) * step
    sb = Decimal(allocation_b.numerator // allocation_b.denominator) * step
    cost = sa + sb
    worst = min(sa * a - cost, sb * b - cost)
    return {"stake_a": sa, "stake_b": sb, "remaining_cash": budget - cost, "worst_profit": worst}


def balanced_two_way(odds_a, odds_b, budget, step=Decimal("0.01")):
    values = [_d(value) for value in (odds_a, odds_b, budget, step)]
    digits = max(
        len(value.as_tuple().digits) + abs(int(value.as_tuple().exponent)) for value in values
    )
    with localcontext() as context:
        context.prec = max(50, 3 * digits + 32)
        return _balanced_two_way(odds_a, odds_b, budget, step)


def implied_probs(decimal_odds):
    odds = [_d(x, "odds") for x in decimal_odds]
    if not 1 <= len(odds) <= 64 or any(x <= 1 for x in odds):
        raise ValueError("decimal odds must exceed one")
    with localcontext() as context:
        context.prec = 50
        return [Decimal(1) / x for x in odds]


def de_vig(decimal_odds):
    raw = implied_probs(decimal_odds)
    with localcontext() as context:
        context.prec = 50
        total = sum(raw)
        probabilities = [x / total for x in raw]
        context.prec = max(
            60,
            2
            * max(len(x.as_tuple().digits) + abs(int(x.as_tuple().exponent)) for x in probabilities)
            + 10,
        )
        probabilities[-1] = Decimal(1) - sum(probabilities[:-1])
        return probabilities


def compare_model_probability(decimal_odds, model_probability):
    odds = _d(decimal_odds, "odds")
    p = _d(model_probability, "model_probability")
    if odds <= 1 or p < 0 or p > 1:
        raise ValueError("invalid odds or probability")
    digits = max(
        len(value.as_tuple().digits) + abs(int(value.as_tuple().exponent)) for value in (odds, p)
    )
    with localcontext() as context:
        context.prec = max(50, 3 * digits + 32)
        return {
            "implied_probability": Decimal(1) / odds,
            "model_probability": p,
            "expected_profit_per_unit": p * odds - 1,
        }
