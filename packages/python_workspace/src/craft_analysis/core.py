from __future__ import annotations

import base64
import hashlib
import json
import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

import numpy as np
import statsmodels.api as sm
import vl_convert as vlc

VERSION = "0.1.0"
MAX_DRAWS = 100_000
MAX_SPEC_BYTES = 200_000
MAX_RENDER_BYTES = 5_000_000
ALLOWED_FORMATS = {"png", "svg"}


class AnalysisError(ValueError):
    pass


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Not JSON serializable: {type(value)!r}")


def _input_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _result(
    method: str,
    payload: Any,
    *,
    units: Any = None,
    assumptions: list[str] | None = None,
    approximation_note: str = "Computed result; do not treat modeled outputs as factual values.",
) -> dict[str, Any]:
    return {
        "method": method,
        "version": VERSION,
        "input_identity": _input_hash(payload),
        "units": units,
        "assumptions": assumptions or [],
        "approximation_note": approximation_note,
    }


def _decimal_string(value: Any) -> str:
    if isinstance(value, bool):
        raise AnalysisError("Boolean is not a valid numeric input.")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise AnalysisError("Empty numeric string.")
        try:
            d = Decimal(text)
        except InvalidOperation as exc:
            raise AnalysisError(f"Invalid decimal string: {value!r}") from exc
    elif isinstance(value, float):
        raise AnalysisError("Float input is rejected; provide exact numeric strings.")
    else:
        raise AnalysisError(f"Unsupported numeric type: {type(value)!r}")
    if not d.is_finite():
        raise AnalysisError("Non-finite numeric value is rejected.")
    if len(d.as_tuple().digits) > 1000 or abs(d.adjusted()) > 1000:
        raise AnalysisError("numeric_input_too_large")
    return str(d)


def _decimal(value: Any) -> Decimal:
    return Decimal(_decimal_string(value))


def _safe_float_from_decimal(d: Decimal) -> float:
    f = float(str(d))
    if not math.isfinite(f):
        raise AnalysisError("Value is not representable as finite float for model computation.")
    return f


def _coerce_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise AnalysisError(f"Invalid ISO date: {value!r}") from exc
    raise AnalysisError("Date must be ISO string or date/datetime object.")


_TAGS = {
    "script",
    "iframe",
    "object",
    "embed",
    "link",
    "img",
    "audio",
    "video",
    "canvas",
    "svg",
    "html",
    "body",
}


def _reject_active_content(text: str) -> None:
    lower = text.lower()
    if any(f"<{tag}" in lower for tag in _TAGS):
        raise AnalysisError("Active HTML/XML content is not allowed.")
    if "javascript:" in lower or "data:text/html" in lower:
        raise AnalysisError("Active content reference is not allowed.")


def compare_parameters(
    left: dict[str, Any], right: dict[str, Any], conversions: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    payload = {"left": left, "right": right, "conversions": conversions}
    left_value = _decimal(left.get("value"))
    right_value = _decimal(right.get("value"))
    left_unit = left.get("unit")
    right_unit = right.get("unit")
    if not isinstance(left_unit, str) or not isinstance(right_unit, str):
        raise AnalysisError("Both left.unit and right.unit must be strings.")

    provenance: list[dict[str, Any]] = []
    comparison_unit = left_unit
    left_comp = left_value
    if left_unit == right_unit:
        right_comp = right_value
        provenance.append(
            {"side": "right", "conversion": "none", "from_unit": right_unit, "to_unit": left_unit}
        )
    else:
        conv = conversions.get(right_unit, {}).get(left_unit)
        if conv is None:
            raise AnalysisError(f"Unknown conversion from {right_unit!r} to {left_unit!r}.")
        factor = _decimal(conv)
        if factor <= 0:
            raise AnalysisError("invalid_conversion_factor")
        with localcontext() as context:
            context.prec = max(
                28, len(right_value.as_tuple().digits) + len(factor.as_tuple().digits) + 10
            )
            right_comp = right_value * factor
        provenance.append(
            {
                "side": "right",
                "conversion": "scale",
                "from_unit": right_unit,
                "to_unit": left_unit,
                "factor": str(factor),
            }
        )
    with localcontext() as context:
        context.prec = max(
            28,
            len(left_comp.as_tuple().digits)
            + len(right_comp.as_tuple().digits)
            + abs(left_comp.adjusted() - right_comp.adjusted())
            + 10,
        )
        diff = left_comp - right_comp
    ratio = None if right_comp == 0 else str(left_comp / right_comp)
    out = _result(
        "compare_parameters",
        payload,
        units={
            "comparison_unit": comparison_unit,
            "left_original": left_unit,
            "right_original": right_unit,
        },
        assumptions=[
            "Numeric inputs are exact Decimals parsed from strings or integers.",
            "Only explicit provided unit conversions are used.",
        ],
        approximation_note="Exact values and unit scaling; ratio division is rounded.",
    )
    out.update(
        {
            "left": {
                "original_value": str(left_value),
                "original_unit": left_unit,
                "compared_value": str(left_comp),
                "compared_unit": comparison_unit,
                "provenance": "input",
            },
            "right": {
                "original_value": str(right_value),
                "original_unit": right_unit,
                "compared_value": str(right_comp),
                "compared_unit": comparison_unit,
                "provenance": provenance,
            },
            "difference": {
                "value": str(diff),
                "unit": comparison_unit,
                "direction": "left_minus_right",
            },
            "ratio_left_to_right": ratio,
        }
    )
    return out


def cost_forecast(rows: list[dict[str, Any]], horizon: int = 3, holdout: int = 2) -> dict[str, Any]:
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= 100:
        raise AnalysisError("invalid_horizon")
    if (
        isinstance(holdout, bool)
        or not isinstance(holdout, int)
        or holdout < 1
        or len(rows) - holdout < 3
    ):
        raise AnalysisError("insufficient_training_data")
    dates = [date.fromisoformat(_coerce_date(row["date"])) for row in rows]
    if any(later <= earlier for earlier, later in zip(dates, dates[1:], strict=False)):
        raise AnalysisError("dates_not_chronological")
    units = {row.get("unit") for row in rows}
    if len(units) != 1 or not all(isinstance(unit, str) and unit for unit in units):
        raise AnalysisError("unit_mismatch")
    unit = next(iter(units))
    exact = [_decimal(row["cost"]) for row in rows]
    if any(value < 0 for value in exact):
        raise AnalysisError("negative_cost")
    values = np.array([_safe_float_from_decimal(value) for value in exact])
    days = np.array([(value - dates[0]).days for value in dates], dtype=float)

    def fit(count):
        return sm.OLS(values[:count], sm.add_constant(days[:count], has_constant="add")).fit()

    trained = fit(len(rows) - holdout)
    predictions = trained.predict(sm.add_constant(days[-holdout:], has_constant="add"))
    mae = float(np.mean(np.abs(values[-holdout:] - predictions)))
    cadence = int(np.median(np.diff(days)))
    future_days = days[-1] + cadence * np.arange(1, horizon + 1)
    final = fit(len(rows))
    prediction = final.get_prediction(
        sm.add_constant(future_days, has_constant="add")
    ).summary_frame(alpha=0.05)
    records = []
    for index, relative in enumerate(future_days):
        mean, low, high = [
            float(prediction.iloc[index][key]) for key in ["mean", "obs_ci_lower", "obs_ci_upper"]
        ]
        if not all(math.isfinite(value) for value in [mean, low, high]):
            raise AnalysisError("nonfinite_forecast")
        records.append(
            {
                "date": date.fromordinal(dates[0].toordinal() + int(relative)).isoformat(),
                "forecast_mean_cost": str(mean),
                "prediction_interval_95": {
                    "lower": str(low),
                    "upper": str(high),
                    "unit": unit,
                    "label": "model-based prediction interval",
                },
            }
        )
    out = _result(
        "cost_forecast",
        {"rows": rows, "horizon": horizon, "holdout": holdout},
        units={"cost_unit": unit, "cadence_days": cadence},
        assumptions=[
            "Linear trend against elapsed calendar days.",
            "Chronological holdout evaluated before refitting all observations.",
            "Future cadence is the median observed day interval.",
        ],
        approximation_note="Floating-point projections; not factual values.",
    )
    out.update(
        {
            "evaluation": {
                "holdout_count": holdout,
                "holdout_mae": str(mae),
                "dates": [value.isoformat() for value in dates[-holdout:]],
                "predicted_costs": [str(float(value)) for value in predictions],
            },
            "forecast": {"horizon": horizon, "rows": records},
        }
    )
    return out


def risk_simulation(
    estimates: list[dict[str, Any]], draws: int = 1000, seed: int = 0
) -> dict[str, Any]:
    payload = {"estimates": estimates, "draws": draws, "seed": seed}
    if isinstance(draws, bool) or not isinstance(draws, int) or draws < 1 or draws > MAX_DRAWS:
        raise AnalysisError(f"draws must be an integer in [1, {MAX_DRAWS}].")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise AnalysisError("seed must be an integer.")
    if not estimates:
        raise AnalysisError("At least one estimate is required.")

    rng = np.random.default_rng(seed)
    total = np.zeros(draws, dtype=np.float64)
    items = []
    unit = None
    for est in estimates:
        name = est.get("name")
        if not isinstance(name, str) or not name:
            raise AnalysisError("Each estimate requires a non-empty string name.")
        lo = _decimal(est.get("min"))
        mode = _decimal(est.get("mode"))
        hi = _decimal(est.get("max"))
        if lo < 0 or mode < 0 or hi < 0:
            raise AnalysisError("Risk estimate values must be nonnegative.")
        if not (lo <= mode <= hi):
            raise AnalysisError("Each estimate must satisfy min <= mode <= max.")
        this_unit = est.get("unit", unit)
        if this_unit is not None and not isinstance(this_unit, str):
            raise AnalysisError("unit must be a string when provided.")
        if unit is None:
            unit = this_unit
        elif this_unit is not None and this_unit != unit:
            raise AnalysisError("All risk estimates must use the same unit.")
        lf = _safe_float_from_decimal(lo)
        mf = _safe_float_from_decimal(mode)
        hf = _safe_float_from_decimal(hi)
        if lf == mf == hf:
            draws_arr = np.full(draws, lf, dtype=np.float64)
            dist = "constant"
        else:
            draws_arr = rng.triangular(lf, mf, hf, size=draws)
            dist = "triangular"
        total += draws_arr
        items.append(
            {
                "name": name,
                "min": str(lo),
                "mode": str(mode),
                "max": str(hi),
                "distribution": dist,
            }
        )
    p10, p50, p90 = np.percentile(total, [10, 50, 90])
    out = _result(
        "risk_simulation",
        payload,
        units={"unit": unit},
        assumptions=[
            "Independent additive components.",
            "Three-point estimates are sampled with numpy triangular distribution unless constant.",
        ],
        approximation_note="Approximate percentiles; reproducible with the same seed.",
    )
    out.update(
        {
            "draws": draws,
            "seed": seed,
            "items": items,
            "summary": {
                "mean_total": str(Decimal(str(float(np.mean(total))))),
                "p10_total": str(Decimal(str(float(p10)))),
                "p50_total": str(Decimal(str(float(p50)))),
                "p90_total": str(Decimal(str(float(p90)))),
            },
        }
    )
    return out


def sensitivity(base: dict[str, Any], perturbations: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {"base": base, "perturbations": perturbations}
    base_input = _decimal(base.get("input"))
    base_output = _decimal(base.get("output"))
    input_unit = base.get("input_unit")
    output_unit = base.get("output_unit")
    if input_unit is not None and not isinstance(input_unit, str):
        raise AnalysisError("base.input_unit must be a string when provided.")
    if output_unit is not None and not isinstance(output_unit, str):
        raise AnalysisError("base.output_unit must be a string when provided.")
    rows = []
    for p in perturbations:
        label = p.get("label")
        if not isinstance(label, str) or not label:
            raise AnalysisError("Each perturbation requires a non-empty label.")
        p_input = _decimal(p.get("input"))
        p_output = _decimal(p.get("output"))
        in_delta = p_input - base_input
        out_delta = p_output - base_output
        elasticity = None
        if base_input != 0 and base_output != 0 and in_delta != 0:
            elasticity = str((out_delta / base_output) / (in_delta / base_input))
        rows.append(
            {
                "label": label,
                "input": {
                    "base": str(base_input),
                    "perturbed": str(p_input),
                    "delta": str(in_delta),
                    "unit": input_unit,
                },
                "output": {
                    "base": str(base_output),
                    "perturbed": str(p_output),
                    "delta": str(out_delta),
                    "unit": output_unit,
                },
                "elasticity": elasticity,
                "options_used": p.get("options", {}),
            }
        )
    out = _result(
        "sensitivity",
        payload,
        units={"input_unit": input_unit, "output_unit": output_unit},
        assumptions=["Caller supplies perturbation outputs and assumptions."],
        approximation_note="Decimal comparisons; elasticity is rounded or omitted when undefined.",
    )
    out.update(
        {"base": {"input": str(base_input), "output": str(base_output)}, "comparisons": rows}
    )
    return out


def chart_spec(records: list[dict[str, Any]], x: str, y: str) -> dict[str, Any]:
    payload = {"records": records, "x": x, "y": y}
    if not isinstance(x, str) or not isinstance(y, str) or not x or not y:
        raise AnalysisError("x and y must be non-empty field names.")
    if not isinstance(records, list):
        raise AnalysisError("records must be a list of objects.")
    clean = []
    for r in records:
        if not isinstance(r, dict):
            raise AnalysisError("Each record must be an object.")
        item = {}
        for k, v in r.items():
            if not isinstance(k, str):
                raise AnalysisError("Record keys must be strings.")
            if isinstance(v, (dict, list, tuple, set, bytes, bytearray)):
                raise AnalysisError(
                    "Nested or binary record values are not allowed in inline chart data."
                )
            if isinstance(v, float) and not math.isfinite(v):
                raise AnalysisError("Non-finite chart values are rejected.")
            if isinstance(v, str):
                _reject_active_content(v)
            item[k] = v
        clean.append(item)
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": clean},
        "mark": "line",
        "encoding": {
            "x": {"field": x, "type": "nominal"},
            "y": {"field": y, "type": "quantitative"},
        },
    }
    raw = json.dumps(spec, separators=(",", ":"), ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_SPEC_BYTES:
        raise AnalysisError("Chart spec exceeds size limit.")
    out = _result(
        "chart_spec",
        payload,
        units=None,
        assumptions=[
            "Chart uses inline Vega-Lite values only.",
            "No external data sources are allowed.",
        ],
        approximation_note="Chart is a visualization spec only.",
    )
    out.update({"spec": spec})
    return out


def render_chart(spec: dict[str, Any], format: str = "png") -> dict[str, Any]:
    payload = {"spec": spec, "format": format}
    if format not in ALLOWED_FORMATS:
        raise AnalysisError(f"Unsupported format {format!r}; allowed: {sorted(ALLOWED_FORMATS)}.")
    if not isinstance(spec, dict):
        raise AnalysisError("spec must be a dict.")

    def check(value):
        if isinstance(value, dict):
            if (
                "url" in value
                or "href" in value
                or value.get("type") == "image"
                or value.get("mark") == "image"
            ):
                raise AnalysisError("external_resource_refused")
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(spec)
    spec_text = json.dumps(spec, separators=(",", ":"), ensure_ascii=False)
    if len(spec_text.encode("utf-8")) > MAX_SPEC_BYTES:
        raise AnalysisError("Chart spec exceeds size limit.")
    _reject_active_content(spec_text)
    if format == "png":
        rendered = vlc.vegalite_to_png(spec, allowed_base_urls=[])
        if len(rendered) > MAX_RENDER_BYTES:
            raise AnalysisError("Rendered output exceeds size limit.")
        content = base64.b64encode(rendered).decode("ascii")
        media_type = "image/png"
        encoding = "base64"
    else:
        rendered = vlc.vegalite_to_svg(spec, allowed_base_urls=[])
        if len(rendered.encode("utf-8")) > MAX_RENDER_BYTES:
            raise AnalysisError("Rendered output exceeds size limit.")
        content = rendered
        media_type = "image/svg+xml"
        encoding = "utf-8"
    out = _result(
        "render_chart",
        payload,
        units=None,
        assumptions=["Inline values; external resource loading is disabled."],
        approximation_note="Rendered chart is a derived artifact of the provided spec.",
    )
    out.update(
        {"format": format, "media_type": media_type, "encoding": encoding, "content": content}
    )
    return out
