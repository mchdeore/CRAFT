"""Decimal source numbers and explicitly registered affine unit conversion."""

import re
from decimal import Decimal, InvalidOperation, localcontext

from app.infrastructure.errors import InvalidInput

NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")


def number(value: str) -> Decimal:
    if not isinstance(value, str) or len(value) > 100 or not NUMBER.fullmatch(value):
        raise InvalidInput()
    try:
        return Decimal(value)
    except InvalidOperation:
        raise InvalidInput() from None


def convert(value: str, unit: str, conversions: dict) -> Decimal:
    if unit not in conversions:
        raise InvalidInput()
    multiplier, offset = conversions[unit]
    source, multiplier, offset = number(value), Decimal(multiplier), Decimal(offset)
    if not multiplier.is_finite() or not offset.is_finite():
        raise InvalidInput()
    source_parts, factor_parts, offset_parts = (
        operand.as_tuple() for operand in (source, multiplier, offset)
    )
    product_exponent = int(source_parts.exponent) + int(factor_parts.exponent)
    offset_exponent = int(offset_parts.exponent)
    precision = (
        max(
            len(source_parts.digits)
            + len(factor_parts.digits)
            + max(0, product_exponent - offset_exponent),
            len(offset_parts.digits) + max(0, offset_exponent - product_exponent),
        )
        + 1
    )
    with localcontext() as context:
        context.prec = max(28, precision)
        return source * multiplier + offset
