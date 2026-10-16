"""Cited table interpretation and Decimal conversion without domain writes."""

import hashlib
import re

import msgspec

from app.cube import units
from app.infrastructure.errors import InvalidInput

PROBLEMS = (
    "unknown header",
    "no unit",
    "unknown unit",
    "not a number",
    "outside the allowed bounds",
    "mission not resolved",
    "no valid-from date",
)
NUMBER = r"[+-]?(?:\d+(?:\.(?!\.)\d*)?|\.\d+)"
CELL = re.compile(rf"^({NUMBER})(?:\s*(?:\.\.|–|—|-|to)\s*({NUMBER}))?\s*(.*?)\s*$")
TOTALS = {"mission total", "mission aggregate", "total", "whole mission"}


def cells(line):
    if not line.startswith("|") or not line.endswith("|"):
        raise InvalidInput()
    values, value, escaped = [], [], False
    for character in line[1:-1]:
        if escaped:
            value.extend([character] if character in {"\\", "|"} else ["\\", character])
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            values.append("".join(value).strip())
            value = []
        else:
            value.append(character)
    if escaped:
        value.append("\\")
    return [*values, "".join(value).strip()]


def header(value):
    unit = re.search(r"\s*\(([^()]*)\)\s*$", value)
    return (
        (value[: unit.start()] if unit else value).strip().lower(),
        unit[1].strip() if unit else None,
    )


def converted(raw, source_unit, parameter):
    stripped = raw.strip()
    kind = (
        "unknown"
        if not stripped or stripped.casefold() == "tbd"
        else ("not_applicable" if stripped.casefold() == "n/a" else "exact")
    )
    matched = CELL.fullmatch(stripped) if kind == "exact" else None
    if re.match(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+", stripped):
        matched = None
    if matched and "\\" in matched[3]:
        matched = None
    inline = matched[3] if matched else ""
    unit = inline or source_unit
    number = (stripped[: matched.start(3)].strip() if inline else stripped) if matched else ""
    if len(number) > 100:
        number = ""
    if parameter is None:
        return kind, number, unit, None, None, "unknown header"
    if not unit:
        return kind, number, None, None, None, "no unit"
    if unit not in parameter["conversions"]:
        return kind, number, unit, None, None, "unknown unit"
    if kind != "exact":
        return kind, number, unit, None, None, None
    if not matched or not number:
        return kind, number, unit, None, None, "not a number"
    first, last = matched[1], matched[2]
    number = stripped[: matched.start(3)].strip() if inline else stripped
    try:
        low = units.convert(first, unit, parameter["conversions"])
        high = units.convert(last, unit, parameter["conversions"]) if last else low
    except InvalidInput:
        return kind, number, unit, None, None, "not a number"
    kind = "range" if last else "exact"
    if (
        low > high
        or parameter["min_value"] is not None
        and low < parameter["min_value"]
        or parameter["max_value"] is not None
        and high > parameter["max_value"]
        or parameter["whole"]
        and (low != low.to_integral_value() or high != high.to_integral_value())
    ):
        return kind, number, unit, None, None, "outside the allowed bounds"
    return kind, number, unit, float(low), float(high), None


def prepare(chunk, dictionary, synonyms, elements, mission, silo, stage, valid_from):
    from app.cube.handlers import Candidate, Candidates

    lines = chunk.text.splitlines()
    if len(lines) < 3:
        raise InvalidInput()
    columns = cells(lines[0])
    separators = cells(lines[1])
    if len(separators) != len(columns) or not all(
        re.fullmatch(r":?-{3,}:?", s) for s in separators
    ):
        raise InvalidInput()
    parsed = [header(value) for value in columns]
    element_columns = not any(key in synonyms for key, _ in parsed) and any(
        value.casefold() in elements for value in columns[1:]
    )
    label = element_columns or parsed[0][0] not in synonyms
    region = re.search(r";range=[A-Z]+(\d+):", chunk.handle.locator)
    window = re.search(r";rows=(\d+)-(\d+)", chunk.handle.locator)
    first = (int(region[1]) if region else 0) + (int(window[1]) if window else 1)
    context = [
        problem
        for condition, problem in [
            (mission is None, "mission not resolved"),
            (valid_from is None, "no valid-from date"),
        ]
        if condition
    ]
    result = []
    for row, line in enumerate(lines[2:], first):
        data = cells(line)
        if len(data) != len(columns):
            raise InvalidInput()
        row_label = data[0] if label else ""
        canonical = elements.get(row_label.casefold(), row_label)
        element = None if not row_label or row_label.casefold() in TOTALS else canonical
        citation = msgspec.structs.replace(
            chunk.handle,
            locator=chunk.handle.locator + f";row={row}",
            quote_sha256=hashlib.sha256(line.encode()).hexdigest(),
        )
        for column in range(1 if label else 0, len(columns)):
            selected_header = row_label if element_columns else columns[column]
            key, unit = header(selected_header)
            matches = synonyms.get(key, [])
            parameter_key = matches[0] if len(matches) == 1 else None
            parameter = dictionary.get(parameter_key) if parameter_key else None
            kind, number, source_unit, low, high, problem = converted(data[column], unit, parameter)
            if element_columns:
                element = elements.get(columns[column].casefold(), columns[column])
                if columns[column].casefold() not in elements:
                    problem = "unknown header"
            result.append(
                Candidate(
                    row=row,
                    header=selected_header,
                    parameter=parameter_key,
                    element=element,
                    source_number=number,
                    source_unit=source_unit,
                    kind=kind,
                    low=low,
                    high=high,
                    citation=citation,
                    problem=problem or (context[0] if context else None),
                )
            )
    problems = set(context) | {candidate.problem for candidate in result if candidate.problem}
    return Candidates(
        mission=mission,
        silo=silo,
        stage=stage,
        valid_from=valid_from,
        candidates=result,
        problems=[problem for problem in PROBLEMS if problem in problems],
    )
