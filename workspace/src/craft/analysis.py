"""Opinionated table and chart helpers for common notebook workflows."""

import csv
import io
import json
from importlib import import_module
from pathlib import Path
from typing import Any

from craft import files

_AGGREGATIONS = {"sum", "mean", "min", "max", "count", "median"}
_CHART_KINDS = {"line", "bar", "scatter"}
_VEGA_MARKS = {"line": {"type": "line", "point": True}, "bar": "bar", "scatter": "point"}


def table(
    frame: Any,
    *,
    columns: list[str] | tuple[str, ...] | None = None,
    rename: dict[str, str] | None = None,
    sort_by: str | None = None,
    ascending: bool = True,
    top: int | None = None,
):
    """Select, rename, sort and trim a table in one step."""
    if top is not None and (not isinstance(top, int) or top < 1):
        raise ValueError("top must be a positive integer")
    result = frame.copy()
    if columns is not None:
        result = result.loc[:, list(columns)]
    if rename:
        result = result.rename(columns=rename)
    if sort_by is not None:
        result = result.sort_values(sort_by, ascending=ascending)
    if top is not None:
        result = result.head(top)
    return result.reset_index(drop=True)


def totals(
    frame: Any,
    by: str | list[str] | tuple[str, ...],
    value: str,
    *,
    agg: str = "sum",
    ascending: bool = False,
    top: int | None = None,
    dropna: bool = False,
):
    """Grouped totals for a numeric column, sorted for quick review."""
    if agg not in _AGGREGATIONS:
        raise ValueError(f"agg must be one of {', '.join(sorted(_AGGREGATIONS))}")
    if top is not None and (not isinstance(top, int) or top < 1):
        raise ValueError("top must be a positive integer")
    keys = [by] if isinstance(by, str) else list(by)
    summary = frame.groupby(keys, as_index=False, dropna=dropna)[value].agg(agg)
    output = value if agg == "sum" else f"{value}_{agg}"
    if output != value:
        summary = summary.rename(columns={value: output})
    summary = summary.sort_values(output, ascending=ascending)
    if top is not None:
        summary = summary.head(top)
    return summary.reset_index(drop=True)


def chart(
    frame: Any,
    x: str,
    y: str,
    *,
    kind: str = "line",
    by: str | None = None,
    title: str | None = None,
    xlabel: str | None = None,
    ylabel: str | None = None,
    figsize: tuple[float, float] = (8, 4),
    save_as: str | Path | None = None,
    engine: str = "matplotlib",
) -> Any:
    """A quick chart from table columns; returns a Figure, a Vega chart or saved path.

    With ``engine="vega"`` the chart is a Vega-Lite spec rendered offline, and
    ``save_as="outputs/charts/name.svg"`` also saves ``name.vl.json`` and ``name.csv``.
    """
    if kind not in _CHART_KINDS:
        raise ValueError(f"kind must be one of {', '.join(sorted(_CHART_KINDS))}")
    if engine == "vega":
        columns = [x, y] + ([by] if by is not None else [])
        encoding: dict[str, Any] = {
            "x": {"field": x, "type": _vega_type(frame[x]), "title": xlabel or x},
            "y": {"field": y, "type": _vega_type(frame[y]), "title": ylabel or y},
        }
        if by is not None:
            encoding["color"] = {"field": by, "type": "nominal", "title": by}
        spec = {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "title": title or f"{y} by {x}",
            "width": int(figsize[0] * 75),
            "height": int(figsize[1] * 75),
            "data": {"values": json.loads(frame.loc[:, columns].to_json(orient="records"))},
            "mark": _VEGA_MARKS[kind],
            "encoding": encoding,
        }
        return vega(spec, save_as=save_as)
    if engine != "matplotlib":
        raise ValueError("engine must be matplotlib or vega")
    plt = import_module("matplotlib.pyplot")

    figure, axes = plt.subplots(figsize=figsize)
    if by is None:
        _draw(axes, frame[x], frame[y], kind)
    else:
        for label, grouped in frame.groupby(by, dropna=False):
            _draw(axes, grouped[x], grouped[y], kind, label=str(label))
        axes.legend(title=by)
    axes.set_title(title or f"{y} by {x}")
    axes.set_xlabel(xlabel or x)
    axes.set_ylabel(ylabel or y)
    figure.tight_layout()
    if save_as is None:
        return figure
    try:
        return files.save(figure, save_as)
    finally:
        plt.close(figure)


def _draw(axes: Any, x_values: Any, y_values: Any, kind: str, *, label: str | None = None) -> None:
    if kind == "line":
        axes.plot(x_values, y_values, marker="o", label=label)
    elif kind == "bar":
        axes.bar(x_values, y_values, label=label)
    else:
        axes.scatter(x_values, y_values, label=label)


class VegaChart:
    """A Vega-Lite chart rendered offline; shows as SVG in a notebook."""

    def __init__(self, spec: dict[str, Any], svg: str):
        self.spec = spec
        self.svg = svg

    def _repr_svg_(self) -> str:
        return self.svg

    def png(self, scale: float = 2) -> bytes:
        convert = import_module("vl_convert")
        return convert.vegalite_to_png(self.spec, scale=scale, allowed_base_urls=[])

    def csv(self) -> str:
        return _spec_table(self.spec)


def vega(spec: dict[str, Any], *, save_as: str | Path | None = None):
    """Render a Vega-Lite spec without network access; returns a chart or saved path.

    Data must be inline (``data.values``); vl-convert, given no allowed base URLs, refuses
    to load any. ``save_as`` ending in ``.svg`` also saves the spec beside it as ``.vl.json``
    and its rows as ``.csv``; ``.png`` saves an image.
    """
    if "url" in (spec.get("data") or {}):  # the clearest message for the usual mistake
        raise ValueError("Give chart data inline as data.values; URLs are not loaded")
    convert = import_module("vl_convert")
    result = VegaChart(spec, convert.vegalite_to_svg(spec, allowed_base_urls=[]))
    if save_as is None:
        return result
    target = files.path(save_as)
    suffix = target.suffix.lower()
    if suffix == ".png":
        return files.save(result.png(), target)
    if suffix != ".svg":
        raise ValueError("Vega charts save as .svg (with .vl.json and .csv) or .png")
    files.save(result.csv(), target.with_suffix(".csv"))
    files.save(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n", target.with_suffix(".vl.json")
    )
    return files.save(result.svg, target)


def _vega_type(column: Any) -> str:
    kind = getattr(column, "dtype", None)
    name = getattr(kind, "kind", "O")
    return "temporal" if name == "M" else "quantitative" if name in "iuf" else "nominal"


def _spec_table(spec: dict[str, Any]) -> str:
    rows: list[dict[str, Any]] = []

    def collect(value: Any) -> None:
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
    out = io.StringIO()
    writer = csv.DictWriter(
        out, list(dict.fromkeys(k for row in rows for k in row)), lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()
