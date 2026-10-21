"""Read, save and query files in the owned workspace by relative name.

Names are relative to ``USER_ROOT``; write ``"folder/name.ext"`` for a
subfolder. The file type follows the extension. Hidden files and symlinks are
outside these helpers.
"""

import json
from pathlib import Path
from typing import Any

from craft import _root


def path(name: str | Path) -> Path:
    """The owned path for a relative name, refusing traversal and symlinks.

    A path this returned is accepted again, so ``save(df, path("a.csv"))`` works.
    """
    name = Path(name)
    if name.is_absolute() and name.is_relative_to(_root.USER_ROOT):
        name = name.relative_to(_root.USER_ROOT)
    return _root.owned_path(name.as_posix(), root=_root.USER_ROOT)


def ls(folder: str = "", pattern: str = "*") -> list[str]:
    """Names directly in a folder, folders ending in ``/``; hidden entries are skipped."""
    base = path(folder) if folder else _root.USER_ROOT
    names = []
    for entry in sorted(base.glob(pattern)):
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        relative = entry.relative_to(_root.USER_ROOT).as_posix()
        names.append(relative + "/" if entry.is_dir() else relative)
    return names


def find(pattern: str = "*") -> list[str]:
    """Every file below the root matching a glob, such as ``"*.xlsx"``."""
    found = []
    for entry in sorted(_root.USER_ROOT.rglob(pattern)):
        relative = entry.relative_to(_root.USER_ROOT)
        if entry.is_file() and not entry.is_symlink():
            if not any(part.startswith(".") for part in relative.parts):
                found.append(relative.as_posix())
    return found


def read(name: str | Path, **options: Any) -> Any:
    """Open a file by type.

    CSV, TSV, Excel and Parquet give a pandas DataFrame (``sheet=`` picks an
    Excel sheet; ``sheet=None`` gives every sheet). JSON and notebooks give
    Python data. Word gives Markdown text, PowerPoint a list of slides and PDF a
    list of page texts. Other text files give a string.
    """
    target = path(name)
    suffix = target.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        import pandas as pd

        return pd.read_csv(target, sep="\t" if suffix == ".tsv" else ",", **options)
    if suffix in {".xlsx", ".xlsm"}:
        import pandas as pd

        sheet = options.pop("sheet", 0)
        return pd.read_excel(target, sheet_name=sheet, **options)
    if suffix == ".parquet":
        import pandas as pd

        return pd.read_parquet(target, **options)
    if suffix in {".json", ".ipynb"}:
        return json.loads(target.read_text(encoding="utf-8"))
    if suffix == ".docx":
        from craft import docs

        return docs.read_word(name)
    if suffix == ".pptx":
        from craft import docs

        return docs.read_slides(name)
    if suffix == ".pdf":
        from craft import docs

        return docs.read_pdf(name)
    return target.read_text(encoding=options.get("encoding", "utf-8"))


def table(name: str | Path, sheet: str | int = 0) -> Any:
    """A spreadsheet or CSV table as a DataFrame, its header found below any title rows.

    The header is the first row as wide as the widest row and made only of text.
    Empty rows and columns are dropped and fully numeric columns become numbers.
    """
    import pandas as pd

    target = path(name)
    suffix = target.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        raw = pd.read_csv(target, sep="\t" if suffix == ".tsv" else ",", header=None, dtype=object)
    else:
        raw = pd.read_excel(target, sheet_name=sheet, header=None, dtype=object)
    raw = raw.dropna(how="all").dropna(axis=1, how="all")
    filled = raw.notna().sum(axis=1)
    header = next(
        (
            index
            for index, row in raw.iterrows()
            if filled[index] == filled.max() and all(isinstance(v, str) for v in row.dropna())
        ),
        filled.idxmax(),
    )
    frame = raw.loc[header:].iloc[1:].copy()
    frame.columns = [
        str(value).strip() if pd.notna(value) else f"column {number}"
        for number, value in enumerate(raw.loc[header], start=1)
    ]
    for column in frame.columns:
        numbers = pd.to_numeric(frame[column], errors="coerce")
        if numbers.notna().sum() == frame[column].notna().sum():
            frame[column] = numbers
    return frame.reset_index(drop=True)


def save(value: Any, name: str | Path, **options: Any) -> str:
    """Save a value by the name's extension and return its relative name.

    DataFrames save as CSV, TSV, Excel, Parquet, JSON or Markdown. Matplotlib
    figures save as PNG, SVG or PDF. Word and PowerPoint objects save as their
    own files. Dictionaries and lists save as JSON; text saves as text. Parent
    folders are created.
    """
    target = path(name)
    suffix = target.suffix.lower()
    if not suffix:
        raise ValueError("Give the name an extension, such as results.csv or chart.png")
    target.parent.mkdir(parents=True, exist_ok=True)
    kind = type(value).__module__.split(".")[0]
    if kind == "pandas":
        frame = value.to_frame() if hasattr(value, "to_frame") and value.ndim == 1 else value
        writers = {
            ".csv": lambda: frame.to_csv(target, index=options.get("index", False)),
            ".tsv": lambda: frame.to_csv(target, sep="\t", index=options.get("index", False)),
            ".xlsx": lambda: _excel({options.get("sheet", "Sheet1"): frame}, target),
            ".parquet": lambda: frame.to_parquet(target, index=options.get("index", False)),
            ".json": lambda: frame.to_json(target, orient="records", indent=2, force_ascii=False),
            ".md": lambda: target.write_text(_markdown(frame), encoding="utf-8"),
        }
        if suffix not in writers:
            raise ValueError(f"Tables save as {', '.join(writers)}")
        writers[suffix]()
    elif kind == "matplotlib":
        figure = getattr(value, "figure", None) or value  # an Axes or a Figure
        figure.savefig(target, bbox_inches="tight", dpi=options.get("dpi", 150))
    elif kind in {"docx", "pptx"}:
        value.save(target)
    elif isinstance(value, dict | list):
        target.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    elif isinstance(value, bytes):
        target.write_bytes(value)
    else:
        target.write_text(str(value), encoding="utf-8")
    return target.relative_to(_root.USER_ROOT).as_posix()


def excel(name: str | Path, **sheets: Any) -> str:
    """Save several DataFrames as named sheets of one workbook."""
    target = path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    _excel(sheets, target)
    return target.relative_to(_root.USER_ROOT).as_posix()


def sql(query: str, **tables: Any):
    """Run DuckDB SQL over DataFrames passed by name and workspace files.

    ``sql("select * from sales where amount > 10", sales=df)`` or
    ``sql("select * from 'data/sales.csv'")``. Relative file names resolve under
    the root. Extensions are never downloaded.
    """
    import duckdb  # pyright: ignore[reportMissingImports]

    connection = duckdb.connect(
        config={
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "file_search_path": str(_root.USER_ROOT),
        }
    )
    try:
        for table, frame in tables.items():
            connection.register(table, frame)
        return connection.sql(query).df()
    finally:
        connection.close()


def _excel(sheets: dict[str, Any], target: Path) -> None:
    import pandas as pd

    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        for sheet, frame in sheets.items():
            frame.to_excel(writer, sheet_name=str(sheet)[:31], index=False)
            columns = writer.sheets[str(sheet)[:31]].columns
            for column, cells in zip(frame.columns, columns, strict=False):
                width = max([len(str(column)), *(len(str(v)) for v in frame[column].head(200))])
                writer.sheets[str(sheet)[:31]].column_dimensions[
                    cells[0].column_letter
                ].width = min(width + 2, 60)


def _markdown(frame: Any) -> str:
    header = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    rule = "|" + "---|" * len(frame.columns)
    rows = [
        "| " + " | ".join(str(v).replace("|", "\\|") for v in row) + " |"
        for row in frame.itertuples(index=False)
    ]
    return "\n".join([header, rule, *rows]) + "\n"
