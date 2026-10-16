"""Source cells become proposals; dictionary, citations, grants and all domain rows stay intact."""

import math
from collections import defaultdict
from uuid import UUID

import msgspec
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.cube.handlers import PrepareValues, prepare_values
from app.cube.intake import PROBLEMS
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import InvalidInput, NotFound
from app.library.handlers import (
    ChunkRange,
    CitationCheck,
    check_citation,
    convert_source,
    index_provider,
    read_chunks,
)
from app.seed.tests.test_demo import demo as demo
from app.seed.vocabulary import PARAMETERS


@pytest.fixture
def intake_source(cube_source):
    source = cube_source
    created_parameters, created_units, created_headers = [], [], []
    with source["owner"].begin() as connection:
        for key, label, unit, minimum, maximum, whole, conversions, synonyms in PARAMETERS:
            if (
                connection.execute(
                    select(m.parameters.c.key).where(m.parameters.c.key == key)
                ).first()
                is None
            ):
                created_parameters.append(key)
                connection.execute(
                    m.parameters.insert().values(
                        key=key,
                        label=label,
                        unit=unit,
                        min_value=minimum,
                        max_value=maximum,
                        whole=whole,
                    )
                )
            for unit, multiplier in conversions.items():
                if (
                    connection.execute(
                        select(m.unit_conversions.c.unit).where(
                            m.unit_conversions.c.parameter == key, m.unit_conversions.c.unit == unit
                        )
                    ).first()
                    is None
                ):
                    created_units.append((key, unit))
                    connection.execute(
                        m.unit_conversions.insert().values(
                            parameter=key, unit=unit, multiplier=multiplier, value_offset=0
                        )
                    )
            for language, header in zip(["en", "fr"], synonyms, strict=True):
                if (
                    connection.execute(
                        select(m.header_synonyms.c.header).where(
                            m.header_synonyms.c.header == header,
                            m.header_synonyms.c.parameter == key,
                        )
                    ).first()
                    is None
                ):
                    created_headers.append((header, key))
                    connection.execute(
                        m.header_synonyms.insert().values(
                            header=header, parameter=key, language=language
                        )
                    )
        connection.execute(
            insert(m.elements)
            .values(mission_key=source["mission"], silo="engineering", name="Payload")
            .on_conflict_do_nothing()
        )
    yield source
    with source["owner"].begin() as connection:
        for header, key in created_headers:
            connection.execute(
                m.header_synonyms.delete().where(
                    m.header_synonyms.c.header == header, m.header_synonyms.c.parameter == key
                )
            )
        for key, unit in created_units:
            connection.execute(
                m.unit_conversions.delete().where(
                    m.unit_conversions.c.parameter == key, m.unit_conversions.c.unit == unit
                )
            )
        if created_parameters:
            connection.execute(
                m.parameters.delete().where(m.parameters.c.key.in_(created_parameters))
            )


def table(source, headers, rows, reference=False):
    path = "reference/intake.md" if reference else source["path"]
    content = (
        "# Simulated table\n\n| "
        + " | ".join(headers)
        + " |\n|"
        + "---|" * len(headers)
        + "\n"
        + "\n".join("| " + " | ".join(row) + " |" for row in rows)
        + "\n"
    ).encode()
    source["provider"].write_content(path, content)
    index_provider(source["settings"])
    item = next(item for item, value in source["provider"].items().items() if value["path"] == path)
    version = source["provider"].get_item(item)["versions"][-1]["version"]
    block = next(block for block in convert_source(content, path) if block.kind == "table")
    handle = (
        read_chunks(
            source["caller"],
            ChunkRange(item=item, version=version, from_block=block.block, to_block=block.block),
        )
        .chunks[0]
        .handle
    )
    return handle


def call(source, handle, **fields):
    return prepare_values(source["caller"], PrepareValues(handle=handle, **fields))


def test_english_and_french_headers(intake_source):
    source = intake_source
    handle = table(
        source, ["Element", "Dry mass (kg)", "Masse sèche (kg)"], [["Mission total", "1", "2"]]
    )
    result = call(source, handle, valid_from="2026-01-01")
    matched = [candidate.parameter for candidate in result.candidates] == [
        "mass_dry",
        "mass_dry",
    ] and not result.problems
    assert matched


def test_conversion(intake_source):
    handle = table(intake_source, ["Element", "Dry mass (kg)"], [["Payload", "450 g"]])
    candidate = call(intake_source, handle, valid_from="2026-01-01").candidates[0]
    matched = (
        candidate.low == 0.45
        and candidate.high == 0.45
        and candidate.source_number == "450"
        and candidate.source_unit == "g"
        and candidate.element == "Payload"
        and candidate.problem is None
    )
    assert matched


def test_each_problem(intake_source):
    source = intake_source
    handle = table(
        source,
        [
            "Element",
            "Mystery (kg)",
            "Dry mass",
            "Dry mass (ZZ)",
            "Dry mass (kg)",
            "Readiness (level)",
            "Peak power (W)",
        ],
        [["Mission total", "1", "2", "3", "unclear", "99", "5"]],
        reference=True,
    )
    result = call(source, handle)
    assert tuple(result.problems) == PROBLEMS
    dated = call(source, handle, mission=source["mission"])
    assert "no valid-from date" in {candidate.problem for candidate in dated.candidates}
    assert all(candidate.problem in PROBLEMS for candidate in result.candidates)


def test_tbd_and_na(intake_source):
    handle = table(
        intake_source,
        ["Element", "Dry mass (kg)", "Peak power (W)", "Readiness (level)"],
        [["Mission total", "TBD", "n/a", ""]],
    )
    result = call(intake_source, handle, valid_from="2026-01-01")
    matched = [candidate.kind for candidate in result.candidates] == [
        "unknown",
        "not_applicable",
        "unknown",
    ] and all(
        candidate.low is None and candidate.high is None and candidate.problem is None
        for candidate in result.candidates
    )
    assert matched


def test_row_citations(intake_source):
    handle = table(
        intake_source, ["Element", "Dry mass (kg)"], [["Mission total", "2"], ["Payload", "1"]]
    )
    result = call(intake_source, handle, valid_from="2026-01-01")
    assert [candidate.row for candidate in result.candidates] == [1, 2]
    assert all(
        check_citation(intake_source["caller"], CitationCheck(handle=candidate.citation)).status
        == "current"
        for candidate in result.candidates
    )


def counts(source):
    names = [
        "silos",
        "stages",
        "missions",
        "mission_aliases",
        "elements",
        "parameters",
        "unit_conversions",
        "header_synonyms",
        "cube_values",
        "facet_texts",
    ]
    with source["owner"].connect() as connection:
        return {
            name: connection.execute(
                select(func.count()).select_from(m.metadata.tables[name])
            ).scalar_one()
            for name in names
        }


def test_writes_nothing(intake_source):
    handle = table(intake_source, ["Element", "Dry mass (kg)"], [["Mission total", "1"]])
    before = counts(intake_source)
    call(intake_source, handle, valid_from="2026-01-01")
    assert counts(intake_source) == before


def test_not_a_table(intake_source):
    source = intake_source
    content = b"# Simulated source\n\nA made-up paragraph.\n"
    source["provider"].write_content(source["path"], content)
    index_provider(source["settings"])
    version = source["provider"].get_item(source["item"])["versions"][-1]["version"]
    handle = (
        read_chunks(
            source["caller"],
            ChunkRange(item=source["item"], version=version, from_block=1, to_block=1),
        )
        .chunks[0]
        .handle
    )
    with pytest.raises(InvalidInput):
        call(source, handle, valid_from="2026-01-01")


def test_ground_truth_values(demo):
    from app.infrastructure.mcp.stdio import local_caller

    caller = local_caller("demo", demo["settings"])
    grouped = defaultdict(list)
    for value in demo["truth"]["values"]:
        citation = value["citation"]
        grouped[
            (citation["item"], citation["version"], citation["block"], value["validFrom"])
        ].append(value)
    compared = 0
    for (item, version, block, valid), values in grouped.items():
        handle = (
            read_chunks(
                caller, ChunkRange(item=item, version=version, from_block=block, to_block=block)
            )
            .chunks[0]
            .handle
        )
        result = prepare_values(caller, PrepareValues(handle=handle, valid_from=valid))
        totals = {
            candidate.parameter: candidate
            for candidate in result.candidates
            if candidate.element is None and candidate.problem is None
        }
        with demo["sessions"].begin() as session:
            for value in values:
                record = (
                    session.execute(
                        select(m.cube_values).where(m.cube_values.c.id == UUID(value["id"]))
                    )
                    .mappings()
                    .one()
                )
                candidate = totals[value["parameter"]]
                matched = (
                    candidate.kind == record["kind"]
                    and candidate.source_unit == record["source_unit"]
                )
                if record["low"] is None:
                    matched = matched and candidate.low is None and candidate.high is None
                else:
                    matched = (
                        matched
                        and math.isclose(candidate.low, float(record["low"]), rel_tol=1e-12)
                        and math.isclose(candidate.high, float(record["high"]), rel_tol=1e-12)
                        and candidate.source_number == record["source_number"]
                    )
                assert matched
                compared += 1
    assert compared == 120


def test_first_parameter_column_and_transposed_elements(intake_source):
    handle = table(intake_source, ["Dry mass (kg)", "Peak power (W)"], [["1", "2"]])
    result = call(intake_source, handle, valid_from="2026-01-01")
    assert len(result.candidates) == 2 and all(
        candidate.element is None for candidate in result.candidates
    )
    handle = table(intake_source, ["Parameter", "Payload"], [["Dry mass (g)", "450"]])
    result = call(intake_source, handle, valid_from="2026-01-01")
    matched = result.candidates[0].element == "Payload" and result.candidates[0].low == 0.45
    assert matched


def test_ranges_whole_bounds_and_invalid_numbers(intake_source):
    handle = table(
        intake_source,
        ["Element", "Dry mass (g)", "Readiness (level)", "Peak power (W)"],
        [["Mission total", "100..200", "4.5", "NaN"], ["Payload", "-1", "3", "Infinity"]],
    )
    result = call(intake_source, handle, valid_from="2026-01-01")
    matched = (
        result.candidates[0].kind == "range"
        and result.candidates[0].low == 0.1
        and result.candidates[0].high == 0.2
    )
    assert matched
    assert [candidate.problem for candidate in result.candidates] == [
        None,
        "outside the allowed bounds",
        "not a number",
        "outside the allowed bounds",
        None,
        "not a number",
    ]


def test_scope_access_and_malformed(intake_source):
    source = intake_source
    handle = table(source, ["Element", "Dry mass (kg)"], [["Mission total", "1"]])
    for fields in [{"mission": "msn-unreadable"}, {"silo": "budget", "mission": "msn-unreadable"}]:
        with pytest.raises(NotFound):
            call(source, handle, **fields)
    with pytest.raises(InvalidInput):
        call(source, handle, valid_from="2026-02-30")
    with pytest.raises(InvalidInput):
        prepare_values(source["caller"], {"handle": handle, "unexpected": "field"})
    wrong = msgspec.structs.replace(handle, quote_sha256="0" * 64)
    with pytest.raises(NotFound):
        call(source, wrong)
    with TestClient(source["app"], base_url="https://testserver") as client:
        assert client.post("/api/cube/candidates", json={}).status_code == 401
        signed = source["sign_in"](client)
        assert signed.status_code == 200
        result = client.post(
            "/api/cube/candidates",
            headers={"Origin": "https://testserver"},
            json={"handle": msgspec.to_builtins(handle), "validFrom": "2026-01-01"},
        )
        assert result.status_code == 200
        assert (
            client.post(
                "/api/cube/candidates",
                headers={"Origin": "https://foreign.invalid"},
                json={"handle": msgspec.to_builtins(handle)},
            ).status_code
            == 403
        )


def test_silo_grant_does_not_expose_hidden_elements(intake_source):
    source = intake_source
    with source["owner"].begin() as connection:
        connection.execute(
            m.mission_grants.update()
            .where(m.mission_grants.c.user_id == source["caller"].user_id)
            .values(silo="engineering")
        )
        connection.execute(
            m.elements.insert().values(
                mission_key=source["mission"], silo="quality", name="Hidden quality element"
            )
        )
    handle = table(
        source, ["Parameter", "hidden quality element"], [["Dry mass (kg)", "1"]], reference=True
    )
    result = call(source, handle, mission=source["mission"], valid_from="2026-01-01")
    assert (
        result.candidates[0].parameter is None and result.candidates[0].problem == "unknown header"
    )
    with pytest.raises(NotFound):
        call(source, handle, mission=source["mission"], silo="quality", valid_from="2026-01-01")


def test_contract_and_response(intake_source):
    import jsonschema

    from app.cube.handlers import Candidate, Candidates, PrepareValues
    from app.tests.test_contract import root_schema, typescript_schema

    handle = table(intake_source, ["Element", "Dry mass (kg)"], [["Mission total", "1"]])
    result = call(intake_source, handle, valid_from="2026-01-01")
    for shape in [PrepareValues, Candidate, Candidates]:
        declared = root_schema(typescript_schema(shape.__name__))
        actual = root_schema(msgspec.json.schema(shape))
        assert set(declared["properties"]) == set(actual["properties"])
        assert set(declared["required"]) == set(actual["required"])
    jsonschema.validate(msgspec.to_builtins(result), typescript_schema("Candidates"))


def test_source_number_limit_and_exponents(intake_source):
    handle = table(
        intake_source,
        ["Element", "Dry mass (kg)"],
        [["Mission total", "9" * 100 + " g"], ["Payload", "1e3"]],
    )
    result = call(intake_source, handle, valid_from="2026-01-01")
    assert len(result.candidates[0].source_number) == 100 and result.candidates[0].problem is None
    assert (
        result.candidates[1].source_number == "" and result.candidates[1].problem == "not a number"
    )


def test_escaped_cells_preserve_source(intake_source):
    from app.cube.intake import cells

    preserved = cells(r"| Mission total | 4\5 |")[-1] == r"4\5"
    assert preserved
    handle = table(
        intake_source,
        ["Element", "Dry mass (kg)"],
        [["Mission total", r"4\5"], [r"Payload\|alpha", "2"]],
    )
    result = call(intake_source, handle, valid_from="2026-01-01")
    assert result.candidates[0].problem == "not a number" and result.candidates[0].low is None
    assert result.candidates[1].element == "Payload|alpha" and result.candidates[1].problem is None
    assert all(
        check_citation(intake_source["caller"], CitationCheck(handle=candidate.citation)).status
        == "current"
        for candidate in result.candidates
    )


def test_example_validates():
    import json
    from pathlib import Path

    import jsonschema

    from app.tests.test_contract import typescript_schema

    jsonschema.validate(
        json.loads(Path("api/examples/candidates.json").read_text()),
        typescript_schema("Candidates"),
    )
