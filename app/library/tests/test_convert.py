"""Synthetic files verify source order, locators, inactive cells and indexing."""

from pathlib import Path

import pytest

from app.library.convert import convert
from app.library.tests.files import (
    csv_file,
    pdf_file,
    slides_file,
    word_file,
    workbook_file,
)


def read(path: Path):
    return convert(path.read_bytes(), path.name)


def test_docx_headings_and_tables(tmp_path) -> None:
    blocks = read(word_file(tmp_path / "simulated.docx"))
    assert [block.kind for block in blocks] == ["text", "table", "text"]
    assert [block.heading_path for block in blocks] == [
        "1 Simulated purpose",
        "1 Simulated purpose",
        "2 Simulated design",
    ]
    assert all(
        block.locator.startswith("heading=") and ";para=" in block.locator for block in blocks
    )
    assert "before the table" in blocks[0].text and "after the table" in blocks[2].text


@pytest.mark.slow
def test_pdf_pages(tmp_path) -> None:
    blocks = read(pdf_file(tmp_path / "simulated.pdf"))
    assert [block.locator for block in blocks] == [
        "page=1;block=1",
        "page=2;block=1",
        "page=3;block=1",
    ]


def test_pdf_without_text_layer(tmp_path) -> None:
    with pytest.raises(ValueError, match="^no text layer$"):
        read(pdf_file(tmp_path / "simulated.pdf", text=False))


def test_pptx_slides_with_notes(tmp_path) -> None:
    blocks = read(slides_file(tmp_path / "simulated.pptx"))
    assert [block.locator for block in blocks] == ["slide=1", "slide=2"]
    assert [block.heading_path for block in blocks] == ["Simulated slide 1", "Simulated slide 2"]
    assert all(f"speaker note {number}" in block.text for number, block in enumerate(blocks, 1))


def test_xlsx_regions_values_only(tmp_path) -> None:
    blocks = read(workbook_file(tmp_path / "simulated.xlsx"))
    assert len(blocks) == 2 and all(block.kind == "table" for block in blocks)
    assert blocks[0].locator == "sheet=Power;range=A1:C3;rows=1-2"
    assert blocks[1].locator == "sheet=Power;range=A5:C6;rows=1-1"
    assert "=1/0" not in blocks[1].text and "| Formula |  | W |" in blocks[1].text


@pytest.mark.parametrize("format", ["docx", "xlsx", "csv"])
def test_long_table_split(tmp_path, format) -> None:
    path = tmp_path / ("simulated." + format)
    if format == "docx":
        word_file(path, rows=75)
    elif format == "xlsx":
        workbook_file(path, rows=75, second_region=False)
    else:
        csv_file(path, rows=75)
    blocks = [block for block in read(path) if block.kind == "table"]
    assert len(blocks) == 3
    header = blocks[0].text.splitlines()[0]
    assert [len(block.text.splitlines()) - 2 for block in blocks] == [30, 30, 15]
    assert all(block.text.splitlines()[0] == header for block in blocks)
    assert [block.locator.rsplit(";rows=", 1)[1] for block in blocks] == ["1-30", "31-60", "61-75"]


def test_csv(tmp_path) -> None:
    blocks = read(csv_file(tmp_path / "simulated.csv"))
    assert len(blocks) == 1 and blocks[0].locator == "sheet=simulated.csv;range=A1:C3;rows=1-2"


@pytest.mark.parametrize(
    "filename", ["simulated.xlsx", "simulated.docx", "simulated.pptx", "simulated.pdf"]
)
def test_mismatched_extension(filename) -> None:
    with pytest.raises(ValueError, match="^unsupported format$"):
        convert(b"Simulated plain text.", filename)


def test_office_package_type_must_match(tmp_path) -> None:
    with pytest.raises(ValueError, match="^unsupported format$"):
        convert(word_file(tmp_path / "simulated.docx").read_bytes(), "simulated.xlsx")


def test_paragraph_split_and_deterministic_handles(tmp_path) -> None:
    from docx import Document

    path = tmp_path / "simulated.docx"
    document = Document()
    document.add_heading("1 Synthetic heading", level=1)
    document.add_paragraph(" ".join(f"synthetic{n}" for n in range(650)))
    document.save(str(path))
    first = read(path)
    second = read(path)
    assert first == second and [len(block.text.split()) for block in first] == [300, 300, 50]
    assert all(block.locator == "heading=1;para=2" for block in first)


def test_multiline_and_pipe_cells_preserve_row_boundaries(tmp_path) -> None:
    from docx import Document

    path = word_file(tmp_path / "simulated.docx")
    document = Document(str(path))
    document.tables[0].cell(1, 0).text = "Mass|probe\npart"
    document.save(str(path))
    table = next(block for block in read(path) if block.kind == "table")
    assert len(table.text.splitlines()) == 4
    assert "Mass\\|probe part" in table.text


def test_invalid_pdf_diagnostics_never_log_source_bytes(tmp_path) -> None:
    import io
    import logging

    from app.infrastructure.logging import configure_logging

    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    parser = logging.getLogger("pypdf")
    original_level = parser.level
    parser.setLevel(logging.DEBUG)
    marker = b"source_object_marker"
    payload = (
        pdf_file(tmp_path / "simulated.pdf").read_bytes().replace(b"1 0 obj", marker + b" obj", 1)
    )
    try:
        with pytest.raises(ValueError, match="^unsupported format$"):
            convert(payload, "simulated.pdf")
        leaked = marker.decode() in stream.getvalue()
        assert not leaked
    finally:
        parser.setLevel(original_level)


def test_pdf_table_repeats_headers_and_limits_rows(tmp_path) -> None:
    from fpdf import FPDF

    document = FPDF()
    document.add_page()
    document.set_font("Helvetica", size=7)
    for row in range(76):
        values = ["Parameter", "Value", "Unit"] if row == 0 else ["Mass", str(row), "kg"]
        for column, value in enumerate(values):
            document.set_xy(20 + column * 50, 20 + row * 2.5)
            document.cell(45, 2.5, value)
    path = tmp_path / "simulated.pdf"
    document.output(path)
    tables = [block for block in read(path) if block.kind == "table"]
    assert len(tables) == 3
    assert [len(block.text.splitlines()) - 2 for block in tables] == [30, 30, 15]
    assert all(block.locator.startswith("page=1;block=") for block in tables)
    assert all(block.text.splitlines()[0] == tables[0].text.splitlines()[0] for block in tables)


def test_slide_tables_follow_header_window_rule(tmp_path) -> None:
    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    table = slide.shapes.add_table(76, 3, Inches(1), Inches(1), Inches(6), Inches(4)).table
    for row in range(76):
        for column, value in enumerate(
            ["Parameter", "Value", "Unit"] if row == 0 else ["Mass", str(row), "kg"]
        ):
            table.cell(row, column).text = value
    path = tmp_path / "simulated.pptx"
    presentation.save(str(path))
    tables = [block for block in read(path) if block.kind == "table"]
    assert [len(block.text.splitlines()) - 2 for block in tables] == [30, 30, 15]
    assert all(block.locator.startswith("slide=1;table=1;rows=") for block in tables)


def test_html_cells_keep_literal_boundaries_and_one_row():
    source = (
        b"<table><tr><th>Mass (kg)</th><th>Label|literal</th></tr>"
        b"<tr><td>1|2</td><td>A<br>B<script>inactive</script></td></tr></table>"
    )
    tables = [block for block in convert(source, "synthetic.html") if block.kind == "table"]
    expected = [r"| Mass (kg) | Label\|literal |", "|---|---|", r"| 1\|2 | A B |"]
    preserved = len(tables) == 1 and tables[0].text.splitlines() == expected
    assert preserved
    assert tables[0].locator.endswith(";rows=1-1")


def test_html_optional_end_tags_keep_rows():
    source = b"<table><tr><th>Mass (kg)<th>Label<tr><td>1<td>A</table>"
    tables = [block for block in convert(source, "synthetic.html") if block.kind == "table"]
    expected = ["| Mass (kg) | Label |", "|---|---|", "| 1 | A |"]
    preserved = len(tables) == 1 and tables[0].text.splitlines() == expected
    assert preserved
