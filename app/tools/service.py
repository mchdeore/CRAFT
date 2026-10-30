"""Bound local rendering and transmit only an allowlisted numeric expression."""

import base64
import csv
import inspect
import io
import json
import math
import re
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import vl_convert
from starlette.concurrency import run_in_threadpool

from app.conversations.handlers import WorkspaceUpload, store_generated_file
from app.infrastructure.errors import ApiError, InvalidInput, NotFound, TooLarge
from app.infrastructure.operations import observe
from app.infrastructure.shapes import FieldError


def capabilities(caller):
    from app.infrastructure.operations.registry import registry, tool_operations
    from app.skills.handlers import SkillList, list_skills
    from app.tools.handlers import Capability, CapabilityCatalog

    tools = [
        Capability(
            name=operation.tool_name,
            description=(inspect.getdoc(operation.function) or "").splitlines()[0][:240],
        )
        for operation in sorted(
            tool_operations(registry, agent=True), key=lambda operation: operation.tool_name
        )
    ]
    catalog = list_skills(caller, SkillList(limit=100))
    return CapabilityCatalog(
        tools=tools,
        skills=[definition.name for definition in catalog.items],
        skills_status="installed",
    )


class NotConfigured(ApiError):
    status, code, message = 503, "not_configured", "The calculation service is not configured."


class Unavailable(ApiError):
    status, code, message = 502, "external_unavailable", "The calculation service is unavailable."


class NotUnderstood(InvalidInput):
    code, message = "not_understood", "The calculation service did not understand the expression."


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


def chart_table(spec):
    """The chart's inline rows as CSV, columns in first-seen order."""
    rows = []

    def collect(value):
        if isinstance(value, dict):
            data = value.get("data")
            if isinstance(data, dict) and isinstance(data.get("values"), list):
                rows.extend(
                    row if isinstance(row, dict) else {"value": row} for row in data["values"]
                )
            for key, child in value.items():
                if key != "data":
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(spec)
    columns = list(dict.fromkeys(key for row in rows for key in row))
    out = io.StringIO()
    writer = csv.DictWriter(out, columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: json.dumps(value) if isinstance(value, dict | list) else value
                for key, value in row.items()
            }
        )
    return out.getvalue()


def titled(spec, title):
    """The spec with its title set, and the title shown for it."""
    if title is not None:
        return {**spec, "title": title}, title
    shown = spec.get("title")
    if isinstance(shown, dict):
        shown = shown.get("text")
    if isinstance(shown, list):
        shown = " ".join(str(part) for part in shown)
    return spec, str(shown or "Chart")[:200]


def checked_sources(caller, sources):
    """Readable cited sources whose quotes still match; a withdrawn or changed one is refused."""
    from app.library.handlers import CitationCheck, check_citation

    for handle in sources:
        try:
            status = check_citation(caller, CitationCheck(handle=handle))
        except NotFound:
            raise InvalidInput() from None
        if status.status not in {"current", "newer_version"}:
            raise InvalidInput()
    return list(sources)


def chart_files(spec):
    """SVG, PNG, Vega-Lite spec and CSV for one validated chart."""
    return {
        "svg": render(spec, "svg"),
        "png": render(spec, "png"),
        "spec": (json.dumps(spec, indent=2, ensure_ascii=False) + "\n").encode(),
        "csv": chart_table(spec).encode(),
    }


def workspace_chart(spec, title):
    spec, _ = titled(spec, title)
    return {
        "svg": render(spec, "svg").decode(),
        "vl.json": json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
        "csv": chart_table(spec),
    }


MEDIA = {"svg": "image/svg+xml", "png": "image/png", "spec": "application/json", "csv": "text/csv"}
SUFFIX = {"svg": "svg", "png": "png", "spec": "vl.json", "csv": "csv"}


async def chart(caller, request):
    spec, title = titled(request.spec, request.title)
    sources = await run_in_threadpool(checked_sources, caller, request.sources)
    simulated = any(handle.simulated for handle in sources)
    media = MEDIA[request.format]
    if caller.conversation_id is None:
        image = await run_in_threadpool(render, spec, request.format)
        return {
            "title": title,
            "format": request.format,
            "media_type": media,
            "content_base64": base64.b64encode(image).decode(),
            "file": None,
            "files": None,
            "citations": sources,
            "simulated": simulated,
        }
    from app.tools.handlers import ChartFiles

    made = await run_in_threadpool(chart_files, spec)
    stem = f"chart-{uuid4().hex}"
    saved = {}
    for kind, content in made.items():
        saved[kind] = await store_generated_file(
            caller,
            WorkspaceUpload(
                id=caller.conversation_id,
                content=content,
                file_name=f"{stem}.{SUFFIX[kind]}",
                media_type=MEDIA[kind],
                simulated=simulated,
            ),
        )
    return {
        "title": title,
        "format": request.format,
        "media_type": media,
        "content_base64": None,
        "file": saved[request.format],
        "files": ChartFiles(**saved),
        "citations": sources,
        "simulated": simulated,
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
                if response.status_code == 501:
                    # Its reply says what it couldn't interpret; it goes back to the caller only.
                    hint = response.text[:500]
                    raise NotUnderstood(fields=[FieldError(path="expression", message=hint)])
                if response.status_code in {401, 403}:
                    raise NotConfigured()  # a wrong or revoked app ID
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
