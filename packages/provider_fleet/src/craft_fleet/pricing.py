"""Configured text-token estimates with explicit price provenance."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from urllib.parse import urlsplit


def safe_id(value: Any) -> str | None:
    return (
        value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) else None
    )


def _decimal(value: Any) -> Decimal:
    if not isinstance(value, (str, Decimal)) or len(str(value)) > 50:
        raise ValueError("invalid_price")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("invalid_price") from None
    if not number.is_finite() or number < 0 or number > Decimal("1000000"):
        raise ValueError("invalid_price")
    if len(number.as_tuple().digits) > 40 or abs(int(number.as_tuple().exponent)) > 40:
        raise ValueError("invalid_price")
    return number


def _provenance(currency, source, as_of):
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("invalid_currency")
    if not isinstance(source, str) or len(source) > 2000:
        raise ValueError("invalid_price_source")
    parts = urlsplit(source)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("invalid_price_source")
    if not isinstance(as_of, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
        raise ValueError("invalid_price_date")
    date.fromisoformat(as_of)


@dataclass(frozen=True)
class Price:
    currency: str
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal
    source: str
    as_of: str

    def __post_init__(self):
        _provenance(self.currency, self.source, self.as_of)
        for value in (
            self.input_per_million,
            self.cached_input_per_million,
            self.output_per_million,
        ):
            _decimal(value)

    @classmethod
    def from_config(cls, value: dict[str, Any]) -> "Price":
        try:
            normal = _decimal(value["input_per_million"])
            cached = _decimal(value.get("cached_input_per_million", str(normal)))
            output = _decimal(value["output_per_million"])
            _provenance(value["currency"], value["source"], value["as_of"])
            return cls(value["currency"], normal, cached, output, value["source"], value["as_of"])
        except (KeyError, TypeError):
            raise ValueError("invalid_price") from None


def estimate_cost(usage: Any, price: Price | None) -> dict[str, Any] | None:
    if price is None or not isinstance(usage, dict):
        return None
    inputs = usage.get("input_tokens", usage.get("prompt_tokens"))
    outputs = usage.get("output_tokens", usage.get("completion_tokens"))
    if any(type(value) is not int or not 0 <= value < 2**63 for value in (inputs, outputs)):
        return None
    assert isinstance(inputs, int) and isinstance(outputs, int)
    details = usage.get("input_tokens_details", usage.get("prompt_tokens_details", {}))
    output_details = usage.get("output_tokens_details", usage.get("completion_tokens_details", {}))
    if not isinstance(details, dict) or not isinstance(output_details, dict):
        return None
    if details.get("audio_tokens", 0) or output_details.get("audio_tokens", 0):
        return None
    cached = details.get("cached_tokens", 0)
    if type(cached) is not int or not 0 <= cached <= inputs:
        return None
    with localcontext() as context:
        context.prec = 100
        amount = (
            (inputs - cached) * price.input_per_million
            + cached * price.cached_input_per_million
            + outputs * price.output_per_million
        ) / Decimal(1000000)
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return {
        "amount": text,
        "currency": price.currency,
        "source": price.source,
        "as_of": price.as_of,
        "estimated": True,
    }


def safe_estimate(value: Any) -> dict[str, Any] | None:
    if (
        not isinstance(value, dict)
        or set(value) != {"amount", "currency", "source", "as_of", "estimated"}
        or value["estimated"] is not True
    ):
        return None
    try:
        amount = value["amount"]
        if not isinstance(amount, str) or len(amount) > 100:
            return None
        number = Decimal(amount)
        if not number.is_finite() or number < 0:
            return None
        _provenance(value["currency"], value["source"], value["as_of"])
    except (ValueError, TypeError, InvalidOperation):
        return None
    return dict(value)
