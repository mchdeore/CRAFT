import base64
from datetime import date, timedelta
from decimal import Decimal

import pytest

from craft_analysis.core import (
    AnalysisError,
    chart_spec,
    compare_parameters,
    cost_forecast,
    render_chart,
    risk_simulation,
    sensitivity,
)


def test_decimal_engineering_comparison_preserves_exact_input():
    value = "1234567890123456789012345678901234567890.1"
    result = compare_parameters(
        {"value": value, "unit": "W"}, {"value": "1", "unit": "kW"}, {"kW": {"W": "1000"}}
    )
    assert result["left"]["original_value"] == value
    assert result["difference"]["value"] == "1234567890123456789012345678901234566890.1"
    assert result["input_identity"] and result["version"]


@pytest.mark.parametrize("value", [float("nan"), "NaN", "Infinity", True, 1.25])
def test_invalid_exact_inputs_are_refused(value):
    with pytest.raises(AnalysisError):
        compare_parameters({"value": value, "unit": "W"}, {"value": "1", "unit": "W"}, {})


def test_unknown_units_are_not_inferred():
    with pytest.raises(AnalysisError):
        compare_parameters({"value": "1", "unit": "W"}, {"value": "1", "unit": "kg"}, {})


def test_forecast_uses_calendar_time_and_held_out_observations():
    start = date(2026, 1, 1)
    days = [0, 1, 3, 4, 6, 9]
    rows = [
        {
            "date": (start + timedelta(days=day)).isoformat(),
            "cost": str(100 + 10 * day),
            "unit": "USD",
        }
        for day in days
    ]
    result = cost_forecast(rows, horizon=2, holdout=2)
    assert float(result["evaluation"]["holdout_mae"]) < 1e-8
    assert result["evaluation"]["dates"] == ["2026-01-07", "2026-01-10"]
    assert (
        result["forecast"]["rows"][0]["prediction_interval_95"]["label"]
        == "model-based prediction interval"
    )
    assert "not factual" in result["approximation_note"]
    with pytest.raises(AnalysisError):
        cost_forecast(list(reversed(rows)))
    with pytest.raises(AnalysisError):
        cost_forecast(rows, horizon=True)


def test_seeded_risk_and_constant_estimates():
    inputs = [
        {"name": "fixed", "min": "5", "mode": "5", "max": "5", "unit": "USD"},
        {"name": "variable", "min": "1", "mode": "2", "max": "3", "unit": "USD"},
    ]
    first = risk_simulation(inputs, draws=100, seed=7)
    assert first == risk_simulation(inputs, draws=100, seed=7)
    summary = first["summary"]
    assert (
        6
        <= float(summary["p10_total"])
        <= float(summary["p50_total"])
        <= float(summary["p90_total"])
        <= 8
    )
    with pytest.raises(AnalysisError):
        risk_simulation(inputs, draws=True)


def test_sensitivity_uses_supplied_outputs():
    result = sensitivity(
        {"input": "10", "output": "20"}, [{"label": "plus", "input": "11", "output": "24"}]
    )
    assert Decimal(result["comparisons"][0]["elasticity"]) == Decimal("2")
    assert result["comparisons"][0]["output"]["delta"] == "4"


def test_chart_png_and_svg_are_local_artifacts():
    spec = chart_spec([{"day": "a", "cost": 1}, {"day": "b", "cost": 2}], "day", "cost")["spec"]
    png = render_chart(spec, "png")
    assert base64.b64decode(png["content"]).startswith(b"\x89PNG")
    svg = render_chart(spec, "svg")
    assert "<svg" in svg["content"] and svg["media_type"] == "image/svg+xml"


def test_nested_external_chart_sources_refused_before_render(monkeypatch):
    import craft_analysis.core as core

    called = []
    monkeypatch.setattr(core.vlc, "vegalite_to_png", lambda *args, **kwargs: called.append(True))
    with pytest.raises(AnalysisError, match="external_resource_refused"):
        render_chart({"layer": [{"data": {"url": "https://synthetic.invalid/data"}}]}, "png")
    assert not called
