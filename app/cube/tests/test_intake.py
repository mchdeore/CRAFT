"""What one cited table turns into: candidates, problems and exact unit conversion."""

import hashlib
from decimal import Decimal

import pytest

from app.cube import intake, units
from app.infrastructure.errors import InvalidInput
from app.library.citation import quote
from app.library.handlers import ChunkText, CitationHandle

ONE, ZERO = Decimal(1), Decimal(0)
GRAMS = {"kg": (ONE, ZERO), "g": (Decimal("0.001"), ZERO)}
DICTIONARY = {
    "mass_dry": {
        "unit": "kg",
        "min_value": ZERO,
        "max_value": None,
        "whole": False,
        "conversions": GRAMS,
    },
    "power_peak": {
        "unit": "W",
        "min_value": ZERO,
        "max_value": Decimal(5000),
        "whole": False,
        "conversions": {"W": (ONE, ZERO), "kW": (Decimal(1000), ZERO)},
    },
    "panel_count": {
        "unit": "count",
        "min_value": ZERO,
        "max_value": None,
        "whole": True,
        "conversions": {"count": (ONE, ZERO)},
    },
}
SYNONYMS = {
    "dry mass": ["mass_dry"],
    "masse sèche": ["mass_dry"],
    "peak power": ["power_peak"],
    "panels": ["panel_count"],
    "power": ["power_peak", "power_average"],
}
ELEMENTS = {"payload": "Payload", "bus": "Bus"}


def table(*lines, locator="heading=1;block=2;rows=1-9"):
    text = "\n".join(lines)
    return ChunkText(
        handle=CitationHandle(
            item="itm_000000000001",
            version=1,
            block=2,
            locator=locator,
            quote_sha256=hashlib.sha256(text.encode()).hexdigest(),
            simulated=True,
        ),
        heading_path="1 Budget",
        text=text,
    )


def prepare(chunk, mission: str | None = "msn-generated", valid_from: str | None = "2026-01-01"):
    return intake.prepare(
        chunk, DICTIONARY, SYNONYMS, ELEMENTS, mission, "engineering", "st3-design", valid_from
    )


def test_cells_convert_to_the_dictionary_unit_and_cite_their_row():
    chunk = table(
        "| Element | Dry mass (g) | Peak power (W) |",
        "|---|---|---|",
        "| Mission total | 450 | 24..30 |",
        "| payload | 2 kg | 0.1–0.2 kW |",
        "| Bus | TBD | n/a |",
    )
    result = prepare(chunk)
    assert result.problems == []
    assert [
        (c.row, c.element, c.parameter, c.kind, c.low, c.high, c.source_number, c.source_unit)
        for c in result.candidates
    ] == [
        (1, None, "mass_dry", "exact", 0.45, 0.45, "450", "g"),
        (1, None, "power_peak", "range", 24.0, 30.0, "24..30", "W"),
        (2, "Payload", "mass_dry", "exact", 2.0, 2.0, "2", "kg"),
        (2, "Payload", "power_peak", "range", 100.0, 200.0, "0.1–0.2", "kW"),
        (3, "Bus", "mass_dry", "unknown", None, None, "", "g"),
        (3, "Bus", "power_peak", "not_applicable", None, None, "", "W"),
    ]
    lines = chunk.text.splitlines()
    for candidate in result.candidates:
        row = lines[candidate.row + 1]
        assert quote(chunk.text, candidate.citation.locator, chunk.handle.locator) == row
        assert hashlib.sha256(row.encode()).hexdigest() == candidate.citation.quote_sha256


@pytest.mark.parametrize(
    ("header", "cell", "problem"),
    [
        ("Dry mass", "12", "no unit"),
        ("Dry mass (lb)", "12", "unknown unit"),
        ("Colour", "red", "unknown header"),
        ("Power (W)", "12", "unknown header"),
        ("Dry mass (kg)", "about 12", "not a number"),
        ("Dry mass (kg)", "1e3", "not a number"),
        ("Dry mass (kg)", "-1", "outside the allowed bounds"),
        ("Dry mass (kg)", "30..24", "outside the allowed bounds"),
        ("Panels (count)", "2.5", "outside the allowed bounds"),
        ("Peak power (kW)", "6", "outside the allowed bounds"),
    ],
)
def test_a_cell_that_cannot_be_read_names_its_problem(header, cell, problem):
    result = prepare(table(f"| Element | {header} |", "|---|---|", f"| Mission total | {cell} |"))
    [candidate] = result.candidates
    assert (candidate.problem, candidate.low, candidate.high) == (problem, None, None)
    assert result.problems == [problem]


def test_an_unresolved_mission_or_date_marks_every_cell_without_its_own_problem():
    chunk = table(
        "| Element | Masse sèche (kg) | Colour |", "|---|---|---|", "| Total | 12 | red |"
    )
    result = prepare(chunk, mission=None, valid_from=None)
    assert [c.problem for c in result.candidates] == ["mission not resolved", "unknown header"]
    assert result.problems == ["unknown header", "mission not resolved", "no valid-from date"]


def test_element_columns_read_each_column_as_an_element():
    chunk = table(
        "| Parameter | Payload | BUS | Tank |",
        "|---|---|---|---|",
        "| Dry mass (kg) | 12 | 30 | 4 |",
    )
    assert [(c.element, c.parameter, c.low, c.problem) for c in prepare(chunk).candidates] == [
        ("Payload", "mass_dry", 12.0, None),
        ("Bus", "mass_dry", 30.0, None),
        ("Tank", "mass_dry", 4.0, "unknown header"),
    ]


def test_spreadsheet_rows_keep_their_sheet_row_numbers():
    chunk = table(
        "| Element | Dry mass (kg) |",
        "|---|---|",
        "| Payload | 12 |",
        "| Bus | 30 |",
        locator="sheet=Budget;range=A1:B40;rows=31-32",
    )
    candidates = prepare(chunk).candidates
    assert [(c.row, c.citation.locator) for c in candidates] == [
        (32, "sheet=Budget;range=A1:B40;rows=31-32;row=32"),
        (33, "sheet=Budget;range=A1:B40;rows=31-32;row=33"),
    ]
    assert [quote(chunk.text, c.citation.locator, chunk.handle.locator) for c in candidates] == [
        "| Payload | 12 |",
        "| Bus | 30 |",
    ]


@pytest.mark.parametrize(
    "lines",
    [
        ["| Element | Dry mass (kg) |", "|---|---|"],
        ["| Element | Dry mass (kg) |", "|---|", "| Bus | 30 |"],
        ["| Element | Dry mass (kg) |", "| x | y |", "| Bus | 30 |"],
        ["| Element | Dry mass (kg) |", "|---|---|", "| Bus | 30 | 4 |"],
        ["| Element | Dry mass (kg) |", "|---|---|", "Bus | 30 |"],
    ],
)
def test_a_malformed_table_is_refused(lines):
    with pytest.raises(InvalidInput):
        prepare(table(*lines))


def test_an_escaped_pipe_stays_inside_its_cell():
    chunk = table("| Element | Dry mass (kg) |", "|---|---|", r"| Tank \| spare | 12 |")
    [candidate] = prepare(chunk).candidates
    assert (candidate.element, candidate.low, candidate.problem) == ("Tank | spare", 12.0, None)


def test_unit_conversion_is_exact_decimal_arithmetic():
    long = "123456789012345678901234567890.5"
    assert units.convert(long, "g", GRAMS) == Decimal("123456789012345678901234567.8905")
    celsius = {"°C": (ONE, Decimal("273.15"))}
    assert units.convert("-20.5", "°C", celsius) == Decimal("252.65")
    for value in ["1e3", "12 ", "", "+", "1.2.3", "NaN", "Infinity", "9" * 101]:
        with pytest.raises(InvalidInput):
            units.convert(value, "g", GRAMS)
    with pytest.raises(InvalidInput):
        units.convert("1", "lb", GRAMS)
