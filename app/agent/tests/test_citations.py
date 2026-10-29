"""Retrieved Cube rows use verified document metadata in answer source cards."""

import hashlib
from copy import deepcopy

import msgspec
import pytest

from app.agent.handlers import AnswerCitation
from app.agent.runtime import citations
from app.library.handlers import ChunkPage, LibraryHit, LibraryHits

ROW = "Duration | 4 | years"
HANDLE = {
    "item": "generated-engineering-item",
    "version": 3,
    "block": 2,
    "locator": "table:2/row:1",
    "quoteSha256": "7d59705e0e11263da93ec74a7c73ac6b682f336e31c5987fbe3612047f6a7364",
    "simulated": True,
}
EXPECTED = {
    "handle": HANDLE,
    "title": "Generated engineering schedule",
    "headingPath": "Schedule / Duration",
    "classification": "protected-a",
    "quote": ROW,
}


@pytest.fixture
def evidence():
    return {
        "value": {
            "id": "00000000-0000-4000-8000-000000000001",
            "mission": "generated-mission",
            "silo": "engineering",
            "stage": "design",
            "element": None,
            "parameter": "duration",
            "kind": "exact",
            "low": 4.0,
            "high": 4.0,
            "unit": "years",
            "sourceNumber": "4",
            "sourceUnit": "years",
            "validFrom": "2026-10-19T00:00:00Z",
            "recordedAt": "2026-10-19T00:00:00Z",
            "citation": deepcopy(HANDLE),
            "withdrawn": False,
            "approvalId": None,
            "simulated": True,
        },
        "quote": ROW,
        "conversion": "4 years = 4 years",
        "citationStatus": "current",
    }


@pytest.fixture
def document():
    return {
        "item": "generated-engineering-item",
        "version": 3,
        "title": "Generated engineering schedule",
        "path": "generated/schedule.txt",
        "tags": {"mission": "generated-mission", "silo": "engineering", "stage": "design"},
        "summary": "Generated schedule fixture.",
        "summaryKind": "extract",
        "outline": [{"block": 2, "headingPath": "Schedule / Duration"}],
        "versions": [{"version": 3, "sha256": "a" * 64, "indexedAt": "2026-10-19T00:00:00Z"}],
        "simulated": True,
        "classification": "protected-a",
        "deleted": False,
    }


@pytest.mark.parametrize("document_first", [False, True])
def test_quoted_cube_row_uses_matching_retrieved_document(evidence, document, document_first):
    results = [document, evidence] if document_first else [evidence, document]

    found = citations(results, "The duration is four years.\n" + ROW)

    assert found == [EXPECTED]
    msgspec.convert(found[0], AnswerCitation)


def test_cube_row_must_be_quoted_in_answer(evidence, document):
    assert citations([evidence, document], "The duration is four years.") == []


def test_cube_quote_hash_must_match(evidence, document):
    evidence["value"]["citation"]["quoteSha256"] = "0" * 64
    assert citations([evidence, document], ROW) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("item", "other-generated-item"),
        ("version", 2),
        ("simulated", False),
        ("classification", "unsupported"),
        ("title", ""),
        ("deleted", True),
        ("outline", []),
        ("outline", [{"block": 4, "headingPath": "Unrelated section"}]),
    ],
)
def test_cube_row_rejects_missing_or_inconsistent_document_metadata(
    evidence, document, field, value
):
    document[field] = value
    assert citations([evidence, document], ROW) == []


@pytest.mark.parametrize("field", ["simulated", "citation"])
def test_cube_row_simulation_must_agree_with_document(evidence, document, field):
    if field == "citation":
        evidence["value"][field]["simulated"] = False
    else:
        evidence["value"][field] = False
    assert citations([evidence, document], ROW) == []


@pytest.mark.parametrize("field", ["classification", "title", "outline"])
def test_conflicting_retrieved_metadata_cannot_supply_cube_source_card(evidence, document, field):
    conflicting = deepcopy(document)
    conflicting[field] = {
        "classification": "protected-b",
        "title": "Other generated title",
        "outline": [{"block": 2, "headingPath": "Other section"}],
    }[field]

    assert citations([document, evidence, conflicting], ROW) == []


def test_cube_row_requires_document_result_not_catalog_metadata(evidence, document):
    catalog = {
        key: document[key] for key in ("item", "version", "title", "classification", "simulated")
    }
    assert citations([evidence, {"documents": [catalog], "truncated": False}], ROW) == []


def test_cube_row_without_document_metadata_has_no_source_card(evidence):
    assert citations([evidence], ROW) == []


def test_nested_unrelated_objects_do_not_become_cube_evidence(evidence, document):
    assert citations([{"unrelated": evidence}, document], ROW) == []
    assert citations([evidence, {"unrelated": document}], ROW) == []


def test_withdrawn_cube_value_has_no_source_card(evidence, document):
    evidence["value"]["withdrawn"] = True
    evidence["citationStatus"] = "withdrawn"
    assert citations([evidence, document], ROW) == []


def test_empty_cube_quote_has_no_source_card(evidence, document):
    evidence["quote"] = ""
    evidence["value"]["citation"]["quoteSha256"] = (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert citations([evidence, document], None) == []


def test_duplicate_cube_results_produce_one_source_card(evidence, document):
    assert citations([evidence, document, deepcopy(evidence), deepcopy(document)], ROW) == [
        EXPECTED
    ]


def test_existing_library_hit_keeps_its_metadata_and_deduplicates_cube_row(evidence, document):
    hit = {
        "kind": "chunk",
        "handle": deepcopy(HANDLE),
        "title": "Existing generated library title",
        "headingPath": "Existing library heading",
        "text": ROW,
        "score": 1.0,
        "tags": deepcopy(document["tags"]),
        "classification": "protected-b",
        "before": None,
        "after": None,
    }

    assert citations([evidence, document, {"hits": [hit], "truncated": False}], ROW) == [
        {
            **EXPECTED,
            "title": hit["title"],
            "headingPath": hit["headingPath"],
            "classification": "protected-b",
        }
    ]


def test_library_hit_without_cube_metadata_remains_supported():
    hit = {
        "handle": deepcopy(HANDLE),
        "text": ROW,
        "title": "Generated existing hit",
        "headingPath": "Existing heading",
        "classification": "unclassified",
    }

    assert citations([{"hits": [hit]}], ROW) == [
        {
            **EXPECTED,
            "title": hit["title"],
            "headingPath": hit["headingPath"],
            "classification": "unclassified",
        }
    ]


def test_cube_row_and_distinct_library_hit_both_remain_cited(evidence, document):
    hit = {
        "handle": {
            **HANDLE,
            "item": "other-generated-item",
            "quoteSha256": "d755b29294f8cc49d5946366e47e2346b2f192af78c2bcf8702254eda000aaa8",
        },
        "text": "Mass | 5 | kg",
        "title": "Generated mass budget",
        "headingPath": "Mass",
        "classification": "unclassified",
    }

    assert citations([evidence, document, {"hits": [hit]}], ROW + "\nMass | 5 | kg") == [
        {
            "handle": hit["handle"],
            "title": "Generated mass budget",
            "headingPath": "Mass",
            "classification": "unclassified",
            "quote": "Mass | 5 | kg",
        },
        EXPECTED,
    ]


def test_cube_evidence_survives_no_text_snapshot_and_resume(evidence, document):
    snapshot = citations([evidence, document], None)
    assert snapshot == [EXPECTED]

    resumed = [dict(row, text=row["quote"]) for row in snapshot]
    assert citations(resumed, ROW) == [EXPECTED]
    assert citations(resumed, "The duration is four years.") == []


@pytest.fixture
def chunk_page():
    return {
        "chunks": [
            {
                "handle": deepcopy(HANDLE),
                "headingPath": "Schedule / Duration",
                "text": ROW,
            }
        ],
        "truncated": False,
    }


@pytest.mark.parametrize("document_first", [False, True])
@pytest.mark.parametrize("simulated", [False, True])
def test_quoted_plain_chunk_uses_matching_document_metadata(
    chunk_page, document, document_first, simulated
):
    chunk_page["chunks"][0]["handle"]["simulated"] = simulated
    document["simulated"] = simulated
    msgspec.convert(chunk_page, ChunkPage)
    results = [document, chunk_page] if document_first else [chunk_page, document]

    found = citations(results, "The duration is four years.\n" + ROW)

    assert found == [{**EXPECTED, "handle": {**HANDLE, "simulated": simulated}}]
    msgspec.convert(found[0], AnswerCitation)


def test_plain_chunk_without_document_has_no_source_card(chunk_page):
    assert citations([chunk_page], ROW) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("item", "other-generated-item"),
        ("version", 2),
        ("simulated", False),
        ("classification", "unsupported"),
        ("title", "   "),
        ("deleted", True),
        ("outline", []),
        ("outline", [{"block": 4, "headingPath": "Unrelated section"}]),
        (
            "outline",
            [
                {"block": 2, "headingPath": "Schedule / Duration"},
                {"block": 2, "headingPath": "Conflicting section"},
            ],
        ),
    ],
)
def test_plain_chunk_rejects_missing_or_inconsistent_document_metadata(
    chunk_page, document, field, value
):
    document[field] = value
    assert citations([chunk_page, document], ROW) == []


@pytest.mark.parametrize("field", ["classification", "title", "outline"])
def test_conflicting_documents_cannot_supply_plain_chunk_card(chunk_page, document, field):
    conflicting = deepcopy(document)
    conflicting[field] = {
        "classification": "protected-b",
        "title": "Other generated title",
        "outline": [{"block": 2, "headingPath": "Other section"}],
    }[field]

    assert citations([document, chunk_page, conflicting], ROW) == []


def test_plain_chunk_requires_document_result_not_catalog_or_nested_metadata(chunk_page, document):
    catalog = {
        key: document[key] for key in ("item", "version", "title", "classification", "simulated")
    }
    assert citations([chunk_page, {"documents": [catalog], "truncated": False}], ROW) == []
    assert citations([chunk_page, {"unrelated": document}], ROW) == []


@pytest.mark.parametrize("missing", ["headingPath", "simulated"])
def test_malformed_plain_chunk_cannot_supply_source_card(chunk_page, document, missing):
    chunk = chunk_page["chunks"][0]
    if missing == "simulated":
        del chunk["handle"][missing]
    else:
        del chunk[missing]

    assert citations([chunk_page, document], ROW) == []


def test_plain_chunk_heading_must_agree_with_document_outline(chunk_page, document):
    chunk_page["chunks"][0]["headingPath"] = "Other section"
    assert citations([chunk_page, document], ROW) == []


def test_plain_chunk_must_be_quoted_in_answer(chunk_page, document):
    assert citations([chunk_page, document], "The duration is four years.") == []


def test_plain_chunk_quote_hash_must_match(chunk_page, document):
    chunk_page["chunks"][0]["handle"]["quoteSha256"] = "0" * 64
    assert citations([chunk_page, document], ROW) == []


def test_empty_plain_chunk_cannot_supply_source_card(chunk_page, document):
    chunk = chunk_page["chunks"][0]
    chunk["text"] = ""
    chunk["handle"]["quoteSha256"] = (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert citations([chunk_page, document], None) == []


def test_duplicate_chunks_and_documents_produce_one_source_card(chunk_page, document):
    assert citations([chunk_page, document, deepcopy(chunk_page), deepcopy(document)], ROW) == [
        EXPECTED
    ]


def test_plain_chunk_survives_no_text_snapshot_and_resume(chunk_page, document):
    snapshot = citations([chunk_page, document], None)
    assert snapshot == [EXPECTED]

    resumed = [dict(row, text=row["quote"]) for row in snapshot]
    assert citations(resumed, ROW) == [EXPECTED]
    assert citations(resumed, "The duration is four years.") == []


@pytest.mark.parametrize("hit_first", [False, True])
def test_existing_library_hit_keeps_precedence_over_plain_chunk(chunk_page, document, hit_first):
    hit = {
        "kind": "chunk",
        "handle": deepcopy(HANDLE),
        "title": "Existing generated library title",
        "headingPath": "Existing library heading",
        "text": ROW,
        "score": 1.0,
        "tags": deepcopy(document["tags"]),
        "classification": "protected-b",
        "before": None,
        "after": None,
    }
    msgspec.convert(hit, LibraryHit)
    results = [{"hits": [hit]}, chunk_page] if hit_first else [chunk_page, {"hits": [hit]}]

    assert citations([document, *results], ROW) == [
        {
            **EXPECTED,
            "title": hit["title"],
            "headingPath": hit["headingPath"],
            "classification": hit["classification"],
        }
    ]


def test_cube_evidence_and_plain_chunk_deduplicate_with_verified_metadata(
    evidence, chunk_page, document
):
    assert citations([evidence, document, chunk_page], ROW) == [EXPECTED]


@pytest.fixture
def search_neighbors(document):
    def chunk(block, text):
        return {
            "handle": {
                **HANDLE,
                "block": block,
                "locator": f"paragraph:{block}",
                "quoteSha256": hashlib.sha256(text.encode()).hexdigest(),
            },
            "headingPath": f"Generated / Block {block}",
            "text": text,
        }

    hit = {
        **chunk(2, "Generated primary retrieval paragraph."),
        "kind": "chunk",
        "title": document["title"],
        "classification": document["classification"],
        "score": 1.0,
        "tags": deepcopy(document["tags"]),
        "before": chunk(1, "Generated neighboring paragraph before."),
        "after": chunk(3, "Generated neighboring paragraph after."),
    }
    result = {"hits": [hit], "truncated": False}
    msgspec.convert(result, LibraryHits)
    return result


def neighbor_card(hit, side):
    neighbor = hit[side]
    return {
        "handle": neighbor["handle"],
        "title": hit["title"],
        "headingPath": neighbor["headingPath"],
        "classification": hit["classification"],
        "quote": neighbor["text"],
    }


@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize("simulated", [False, True])
def test_quoted_search_neighbor_uses_parent_metadata_without_document(
    search_neighbors, side, simulated
):
    hit = search_neighbors["hits"][0]
    for chunk in (hit, hit["before"], hit["after"]):
        chunk["handle"]["simulated"] = simulated
    msgspec.convert(search_neighbors, LibraryHits)

    found = citations([search_neighbors], hit[side]["text"])

    assert found == [neighbor_card(hit, side)]
    msgspec.convert(found[0], AnswerCitation)


@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize(
    "field,value", [("item", "other-generated-item"), ("version", 4), ("simulated", False)]
)
def test_search_neighbor_requires_matching_parent_context(search_neighbors, side, field, value):
    hit = search_neighbors["hits"][0]
    hit[side]["handle"][field] = value
    msgspec.convert(search_neighbors, LibraryHits)

    assert citations([search_neighbors], hit[side]["text"]) == []


@pytest.mark.parametrize(
    "field,value",
    [("title", "   "), ("classification", "unsupported"), ("tags", None), ("kind", "document")],
)
def test_search_neighbor_requires_valid_parent_metadata(search_neighbors, field, value):
    hit = search_neighbors["hits"][0]
    hit[field] = value

    assert citations([search_neighbors], hit["before"]["text"]) == []


@pytest.mark.parametrize("side", ["before", "after"])
def test_search_neighbor_requires_verbatim_quote_and_matching_hash(search_neighbors, side):
    hit = search_neighbors["hits"][0]
    assert citations([search_neighbors], "Generated answer without the neighbor.") == []
    hit[side]["handle"]["quoteSha256"] = "0" * 64
    assert citations([search_neighbors], hit[side]["text"]) == []


@pytest.mark.parametrize("side", ["before", "after"])
def test_empty_search_neighbor_cannot_supply_source_card(search_neighbors, side):
    hit = search_neighbors["hits"][0]
    hit[side]["text"] = ""
    hit[side]["handle"]["quoteSha256"] = hashlib.sha256(b"").hexdigest()

    assert citations([search_neighbors], "") == []


@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize(
    "field,value", [("title", "Conflicting title"), ("classification", "protected-b")]
)
def test_search_neighbor_rejects_conflicting_parent_metadata(search_neighbors, side, field, value):
    conflicting = deepcopy(search_neighbors)
    conflicting["hits"][0][field] = value
    quote = search_neighbors["hits"][0][side]["text"]

    assert citations([search_neighbors, conflicting], quote) == []
    assert citations([conflicting, search_neighbors], quote) == []


@pytest.mark.parametrize("side", ["before", "after"])
def test_search_neighbors_deduplicate_and_survive_snapshot_resume(search_neighbors, side):
    hit = search_neighbors["hits"][0]
    snapshot = citations([search_neighbors, deepcopy(search_neighbors)], None)
    assert len(snapshot) == 3
    assert neighbor_card(hit, side) in snapshot
    for row in snapshot:
        msgspec.convert(row, AnswerCitation)
    resumed = [dict(row, text=row["quote"]) for row in snapshot]
    assert citations(resumed, hit[side]["text"]) == [neighbor_card(hit, side)]


def test_plain_chunk_cannot_borrow_unrelated_search_neighbor_metadata(chunk_page, search_neighbors):
    assert citations([chunk_page, search_neighbors], ROW) == []


@pytest.mark.parametrize("side", ["before", "after"])
def test_labelled_hit_keeps_precedence_over_search_neighbor(search_neighbors, side):
    hit = search_neighbors["hits"][0]
    labelled = {
        **hit,
        **deepcopy(hit[side]),
        "title": "Generated labelled neighbor",
        "classification": "protected-b",
        "before": None,
        "after": None,
    }
    msgspec.convert(labelled, LibraryHit)
    result = {"hits": [labelled], "truncated": False}
    expected = [
        {**neighbor_card(hit, side), "title": labelled["title"], "classification": "protected-b"}
    ]

    assert citations([search_neighbors, result], hit[side]["text"]) == expected
    assert citations([result, search_neighbors], hit[side]["text"]) == expected


def hit(block: int, text: str) -> dict:
    return {
        "text": text,
        "title": "Generated power budget",
        "handle": {
            "item": "itm_generated",
            "version": 1,
            "block": block,
            "quoteSha256": hashlib.sha256(text.encode()).hexdigest(),
            "simulated": True,
        },
    }


def test_a_source_link_cites_only_its_own_block() -> None:
    """A paraphrase cites by link; block 12's link must not also cite block 1."""
    results = [hit(1, "Peak power is 400 W."), hit(12, "Dry mass is 120 kg.")]
    reply = "La masse sèche est de 120 kg [budget](/sources/itm_generated?version=1&block=12)."
    assert [row["quote"] for row in citations(results, reply, linked=True)] == [
        "Dry mass is 120 kg."
    ]
    assert citations(results, reply) == []
