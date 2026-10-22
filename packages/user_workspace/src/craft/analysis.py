"""Opinionated table and chart helpers for common notebook workflows."""

from importlib import import_module
from pathlib import Path
from typing import Any

from craft import files

_AGGREGATIONS = {"sum", "mean", "min", "max", "count", "median"}
_CHART_KINDS = {"line", "bar", "scatter"}


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
):
    """A quick chart from table columns; returns a Figure or saved path."""
    if kind not in _CHART_KINDS:
        raise ValueError(f"kind must be one of {', '.join(sorted(_CHART_KINDS))}")
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
