"""Bound local rendering and transmit only an allowlisted numeric expression."""

import base64
import math
import re
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import vl_convert
from starlette.concurrency import run_in_threadpool

from app.conversations.handlers import WorkspaceUpload, store_generated_file
from app.infrastructure.errors import ApiError, InvalidInput, TooLarge
from app.infrastructure.operations import observe


class NotConfigured(ApiError):
    status, code, message = 503, "not_configured", "The calculation service is not configured."


class Unavailable(ApiError):
    status, code, message = 502, "external_unavailable", "The calculation service is unavailable."


UNITS = frozenset(
    (
        "m km cm mm ft in kg g lb lbm lbf tonne N kN s ms min h Pa kPa MPa psi "
        "K C F degC degF W kW MW J kJ MJ CAD USD"
    ).split()
)
FUNCTIONS = frozenset("sqrt abs sin cos tan log ln exp min max sum mean pi e to".split())


def validate_chart(spec):
    rows = 0
    has_data = False

    def inspect(value):
        nonlocal rows, has_data
        if isinstance(value, dict):
            if any(k in value for k in ("url", "href", "datasets")):
                raise InvalidInput()
            if value.get("mark") == "image" or value.get("type") == "image":
                raise InvalidInput()
            if "data" in value:
                data = value["data"]
                if (
                    not isinstance(data, dict)
                    or set(data) != {"values"}
                    or not isinstance(data["values"], list)
                ):
                    raise InvalidInput()
                rows += len(data["values"])
                has_data = True
            for key, child in value.items():
                if (
                    key in {"width", "height"}
                    and isinstance(child, (int, float))
                    and not 0 < child <= 2048
                ):
                    raise InvalidInput()
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(spec)
    if not has_data or rows > 5000:
        raise InvalidInput()


@observe("tools.chart_render")
def render(spec, kind):
    validate_chart(spec)
    try:
        if kind == "svg":
            result = vl_convert.vegalite_to_svg(spec, allowed_base_urls=[]).encode()
        else:
            result = vl_convert.vegalite_to_png(spec, allowed_base_urls=[])
    except (ValueError, RuntimeError, TypeError):
        raise InvalidInput() from None
    if len(result) > 25 * 1024 * 1024:
        raise TooLarge()
    return result


async def chart(caller, request):
    image = await run_in_threadpool(render, request.spec, request.format)
    media = "image/svg+xml" if request.format == "svg" else "image/png"
    saved = None
    if caller.conversation_id is not None:
        saved = await store_generated_file(
            caller,
            WorkspaceUpload(
                id=caller.conversation_id,
                content=image,
                file_name=f"chart-{uuid4().hex}.{request.format}",
                media_type=media,
            ),
        )
    return {
        "format": request.format,
        "media_type": media,
        "content_base64": base64.b64encode(image).decode() if saved is None else None,
        "file": saved,
    }


def validate_expression(expression, unit):
    if not re.fullmatch(r"[0-9A-Za-z.+*/^%(), \t-]+", expression):
        raise InvalidInput()
    without_exponents = re.sub(r"[0-9]+(?:\.[0-9]+)?[eE][-+]?[0-9]+", "", expression)
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9]*", without_exponents)
    if any(token not in UNITS | FUNCTIONS for token in tokens):
        raise InvalidInput()
    if unit is not None and unit not in UNITS:
        raise InvalidInput()


def numeric(value):
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    match = re.fullmatch(
        r"\s*([-+]?[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?)(?:\s+([A-Za-z]+))?\s*",
        value or "",
    )
    if match and (match[2] is None or match[2] in UNITS):
        number = float(match[1].replace(",", ""))
        return number if math.isfinite(number) else None
    return None


def agrees(left, right):
    if left is None:
        return False
    left_unit = re.search(r"\s+([A-Za-z]+)\s*$", str(left))
    right_unit = re.search(r"\s+([A-Za-z]+)\s*$", str(right))
    if left_unit and right_unit and left_unit[1] != right_unit[1]:
        return False
    a, b = numeric(left), numeric(right)
    if a is not None and b is not None:
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=0)
    return " ".join(str(left).split()) == " ".join(str(right).split())


@observe("tools.calculation_request")
async def request_wolfram(expression, app_id):
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
        for attempt in range(2):
            try:
                response = await client.get(
                    "https://www.wolframalpha.com/api/v1/llm-api",
                    params={"input": expression},
                    headers={"Authorization": "Bearer " + app_id},
                )
                response.raise_for_status()
                if not response.text or len(response.content) > 1024 * 1024:
                    raise Unavailable()
                return response.text
            except httpx.HTTPError:
                if attempt == 1:
                    raise Unavailable() from None
    raise Unavailable()


async def calculation(caller, request):
    validate_expression(request.expression, request.unit)
    if not caller.settings.wolfram_app_id:
        raise NotConfigured()
    result = await request_wolfram(request.expression, caller.settings.wolfram_app_id)
    return {
        "expression": request.expression,
        "wolfram_result": result,
        "craft_result": request.craft_result,
        "agrees": agrees(request.craft_result, result),
        "checked_at": datetime.now(UTC).isoformat(),
    }
