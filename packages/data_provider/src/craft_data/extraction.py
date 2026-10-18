"""Small standard-library extractors for generated local fixtures."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Iterable
from xml.etree import ElementTree

from .models import BlockInput


def extract_plain_text(body: bytes) -> tuple[BlockInput, ...]:
    text = body.decode("utf-8-sig")
    blocks: list[BlockInput] = []
    current_heading: str | None = None
    paragraph: list[str] = []
    start_line = 1

    def flush() -> None:
        nonlocal paragraph
        content = "\n".join(line.rstrip() for line in paragraph).strip()
        if content:
            blocks.append(
                BlockInput(
                    text=content,
                    location=f"line:{start_line}",
                    heading=current_heading,
                )
            )
        paragraph = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        heading_match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading_match:
            flush()
            current_heading = heading_match.group(1)
            start_line = line_number
        elif not line.strip():
            flush()
            start_line = line_number + 1
        else:
            if not paragraph:
                start_line = line_number
            paragraph.append(line)
    flush()
    return tuple(blocks)


def extract_csv(body: bytes, *, encoding: str = "utf-8-sig") -> tuple[BlockInput, ...]:
    rows = list(csv.reader(io.StringIO(body.decode(encoding), newline="")))
    if not rows:
        return ()
    headers = rows[0]
    blocks: list[BlockInput] = []
    for row_number, row in enumerate(rows[1:], start=2):
        values = []
        for index, value in enumerate(row):
            heading = (
                headers[index] if index < len(headers) and headers[index] else f"column_{index + 1}"
            )
            values.append(f"{heading}={value}")
        blocks.append(
            BlockInput(
                text=" | ".join(values),
                location=f"row:{row_number}",
                heading="CSV row",
            )
        )
    return tuple(blocks)


_DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_XLSX_NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def extract_generated_docx_subset(body: bytes) -> tuple[BlockInput, ...]:
    """Read paragraphs and simple table rows from generated DOCX fixtures."""
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))

    blocks: list[BlockInput] = []
    paragraph_number = 0
    table_number = 0
    for child in root.findall(".//w:body/*", _DOCX_NS):
        if child.tag.endswith("}p"):
            paragraph_number += 1
            text = "".join(node.text or "" for node in child.findall(".//w:t", _DOCX_NS))
            style = child.find("w:pPr/w:pStyle", _DOCX_NS)
            style_name = style.get(f"{{{_DOCX_NS['w']}}}val", "") if style is not None else ""
            heading = text if style_name.casefold().startswith("heading") else None
            if text:
                blocks.append(
                    BlockInput(
                        text=text,
                        location=f"paragraph:{paragraph_number}",
                        heading=heading,
                    )
                )
        elif child.tag.endswith("}tbl"):
            table_number += 1
            for row_number, row in enumerate(child.findall("w:tr", _DOCX_NS), start=1):
                cells = []
                for cell_number, cell in enumerate(row.findall("w:tc", _DOCX_NS), start=1):
                    value = " ".join(node.text or "" for node in cell.findall(".//w:t", _DOCX_NS))
                    cells.append(f"cell_{cell_number}={value}")
                if cells:
                    blocks.append(
                        BlockInput(
                            text=" | ".join(cells),
                            location=f"table:{table_number},row:{row_number}",
                            heading="Table row",
                        )
                    )
    return tuple(blocks)


def extract_generated_xlsx_subset(body: bytes) -> tuple[BlockInput, ...]:
    """Read shared strings and the first worksheet from generated XLSX fixtures."""
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [
                "".join(node.text or "" for node in item.findall(".//a:t", _XLSX_NS))
                for item in shared_root.findall("a:si", _XLSX_NS)
            ]
        worksheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))

    blocks: list[BlockInput] = []
    for row in worksheet.findall(".//a:sheetData/a:row", _XLSX_NS):
        cells: list[str] = []
        for cell in row.findall("a:c", _XLSX_NS):
            reference = cell.get("r", "?")
            value_node = cell.find("a:v", _XLSX_NS)
            inline_node = cell.find("a:is", _XLSX_NS)
            if inline_node is not None:
                value = "".join(item.text or "" for item in inline_node.findall(".//a:t", _XLSX_NS))
            elif value_node is None:
                value = ""
            elif cell.get("t") == "s":
                index = int(value_node.text or "0")
                value = shared[index] if index < len(shared) else ""
            else:
                value = value_node.text or ""
            cells.append(f"{reference}={value}")
        if cells:
            row_number = row.get("r", "?")
            blocks.append(
                BlockInput(
                    text=" | ".join(cells),
                    location=f"Sheet1!row:{row_number}",
                    heading="Spreadsheet row",
                )
            )
    return tuple(blocks)


def checked_blocks(blocks: Iterable[BlockInput]) -> tuple[BlockInput, ...]:
    result = tuple(blocks)
    if len(result) > 100_000:
        raise ValueError("block count exceeds the local prototype limit")
    if any(not isinstance(block, BlockInput) for block in result):
        raise TypeError("extractors must return BlockInput records")
    return result
