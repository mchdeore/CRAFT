"""Find, get, compare and similar missions read only what the caller's grants reach.

The access test at the end pins each read's answer for four kinds of caller, library reads too.
"""

from dataclasses import replace
from uuid import uuid4

import msgspec
import pytest

from app.cube.handlers import (
    CompareMissions,
    Condition,
    FindMissions,
    MissionRef,
    PrepareValues,
    SimilarMissions,
    compare_missions,
    find_missions,
    get_mission,
    prepare_values,
    similar_missions,
)
from app.infrastructure.errors import NotFound
from app.library.handlers import (
    ChunkRange,
    DocumentList,
    LibrarySearch,
    RelationsRequest,
    list_documents,
    read_chunks,
    relations,
    search,
)
from app.tests.support import rows
from app.tests.support.cube import BUDGET
from app.tests.support.cube import cube_source as cube_source


def above(watts):
    condition = Condition(parameter="power_peak", op="ge", value=str(watts), unit="W")
    return FindMissions(conditions=[condition])


def found(caller, request):
    result = find_missions(caller, request)
    return {row.mission.key: [value.id for value in row.matched] for row in result.missions}


def compared(caller, request):
    rows = compare_missions(caller, request).rows
    return [[value.id if value else None for value in row.values] for row in rows]


def test_a_silo_grant_limits_find_get_and_compare_to_that_silo(cube):
    a, b = cube["mission"], cube["other"]
    engineering_a = cube["value"](a, "engineering", 400)
    budget_a = cube["value"](a, "budget", 900)
    engineering_b = cube["value"](b, "engineering", 350)
    everyone = cube["member"]((a, None), (b, None))
    engineers = cube["member"]((a, "engineering"), (b, "engineering"))
    budget = cube["member"]((a, "budget"))

    assert found(everyone, above(800)) == {a: [budget_a]}
    assert found(engineers, above(800)) == {}
    assert found(engineers, above(300)) == {a: [engineering_a], b: [engineering_b]}
    assert found(budget, above(300)) == {a: [budget_a]}
    # A mission matched in two silos shows one: the first silo by name.
    assert found(everyone, above(300)) == {a: [budget_a], b: [engineering_b]}

    def values(caller, key):
        return [value.id for value in get_mission(caller, MissionRef(key=key)).values]

    assert sorted(values(everyone, a)) == sorted([engineering_a, budget_a])
    assert values(engineers, a) == [engineering_a]
    assert values(budget, a) == [budget_a]
    with pytest.raises(NotFound):
        get_mission(budget, MissionRef(key=b))

    both = CompareMissions(keys=[a, b], parameters=["power_peak"])
    assert compared(engineers, both) == [[engineering_a], [engineering_b]]
    # Two silos' totals for one mission aren't comparable until the scope picks a silo.
    assert compared(everyone, both) == [[None], [engineering_b]]
    engineering = msgspec.structs.replace(both, silo="engineering")
    assert compared(everyone, engineering) == [[engineering_a], [engineering_b]]
    with pytest.raises(NotFound):
        compare_missions(budget, both)


def test_similar_missions_score_readable_facet_evidence(cube):
    a, b = cube["mission"], cube["other"]
    reader = cube["member"]((a, None), (b, None))
    cube["facet"](a, "design", [1.0])
    cube["facet"](a, "operations", [0.0, 1.0])
    cube["facet"](b, "design", [1.0])
    cube["facet"](b, "operations", [0.0, 1.0, 1.0])
    two = SimilarMissions(key=a, facets=["design", "operations"])

    [match] = similar_missions(reader, two).missions
    assert (match.mission.key, match.comparable) == (b, True)
    assert match.facet_scores == pytest.approx({"design": 1.0, "operations": 0.5**0.5})
    assert match.score == pytest.approx((1 + 0.5**0.5) / 2)

    # Two shared facets out of five are too few to compare.
    [match] = similar_missions(reader, SimilarMissions(key=a)).missions
    assert (match.comparable, match.score) == (False, None)

    # Evidence whose source was withdrawn no longer counts.
    cube["provider"].delete_item(cube["documents"][b])
    [match] = similar_missions(reader, two).missions
    assert (match.comparable, match.score) == (False, None)
    assert match.facet_scores == {"design": None, "operations": None}

    with pytest.raises(NotFound):
        similar_missions(reader, SimilarMissions(key="msn-unknown"))


# Today's answers, kept on purpose (X4, agreed 2026-10-29). The document rule reads every document
# to an admin and a document outside every mission to everyone; the cube rule and the mission
# listing need a grant, for admins too. Relations use the document rule for documents and the cube
# rule for values.
EXPECTED = {
    "admin": {
        "catalog": ["a-budget", "a-engineering", "b-engineering", "handbook"],
        "search": ["a-budget", "a-engineering", "b-engineering", "handbook"],
        "find": {},
        "similar": "not found",
        # Without a readable mission, intake goes on with the mission unresolved.
        "intake": {"budget silo": (None, 2), "no silo": "not found"},
        "relations": [
            "a-budget",
            "a-budget@1",
            "a-engineering",
            "a-engineering@1",
            "facet:facet-a",
            "mission:a",
        ],
    },
    "mission-wide": {
        "catalog": ["a-budget", "a-engineering", "b-engineering", "handbook"],
        "search": ["a-budget", "a-engineering", "b-engineering", "handbook"],
        "find": {"a": ["value-a-budget"], "b": ["value-b-engineering"]},
        "similar": [("b", {"design": 1.0, "operations": 1.0})],
        "intake": {"budget silo": ("a", 2), "no silo": ("a", 2)},
        "relations": [
            "a-budget",
            "a-budget@1",
            "a-engineering",
            "a-engineering@1",
            "facet:facet-a",
            "mission:a",
            "value:value-a-budget",
            "value:value-a-engineering",
        ],
    },
    "silo-limited": {
        "catalog": ["a-engineering", "b-engineering", "handbook"],
        "search": ["a-engineering", "b-engineering", "handbook"],
        "find": {"a": ["value-a-engineering"], "b": ["value-b-engineering"]},
        # The operations facet cites the budget document only, which the silo grant can't read.
        "similar": [("b", {"design": 1.0, "operations": None})],
        "intake": {"budget silo": "not found", "no silo": ("a", 2)},
        "relations": [
            "a-engineering",
            "a-engineering@1",
            "facet:facet-a",
            "mission:a",
            "value:value-a-engineering",
        ],
    },
    "no grant": {
        "catalog": ["handbook"],
        "search": ["handbook"],
        "find": {},
        "similar": "not found",
        "intake": {"budget silo": "not found", "no silo": "not found"},
        "relations": "not found",
    },
}


@pytest.mark.parametrize("who", list(EXPECTED))
def test_each_caller_reads_what_the_grant_rules_give_it(cube, who):
    a, b, provider = cube["mission"], cube["other"], cube["provider"]
    plan = f"missions/{a}/budget/st3-design/plan.md"
    provider.write_content(plan, b"# Plan\n\nPeak power budget.\n")
    provider.write_content("library/handbook.md", BUDGET.replace(b"350", b"500"))
    rows.load(cube["owner"], provider)
    paths = {record["path"]: key for key, record in provider.items().items()}
    names = {
        a: "a",
        b: "b",
        cube["documents"][a]: "a-engineering",
        paths[plan]: "a-budget",
        cube["documents"][b]: "b-engineering",
        paths["library/handbook.md"]: "handbook",
        cube["value"](a, "engineering", 400): "value-a-engineering",
        cube["value"](a, "budget", 900): "value-a-budget",
        cube["value"](b, "engineering", 350): "value-b-engineering",
        cube["facet"](a, "design", [1.0]): "facet-a",
        cube["facet"](b, "design", [1.0]): "facet-b",
        cube["facet"](a, "operations", [0.0, 1.0], [paths[plan]]): "facet-a-operations",
        cube["facet"](b, "operations", [0.0, 1.0]): "facet-b-operations",
    }
    names = {str(key): name for key, name in names.items()}
    caller = {
        "admin": lambda: replace(cube["caller"], user_id=uuid4(), roles=frozenset({"admin"})),
        "mission-wide": lambda: cube["member"]((a, None), (b, None)),
        "silo-limited": lambda: cube["member"]((a, "engineering"), (b, "engineering")),
        "no grant": lambda: cube["member"](),
    }[who]()

    def named(keys):
        return sorted(names[str(key)] for key in keys if str(key) in names)

    def node(identifier):
        for key, name in names.items():
            identifier = identifier.replace(key, name)
        return identifier

    def answer(read):
        try:
            return read()
        except NotFound:
            return "not found"

    def table(item):
        page = ChunkRange(item=item, version=1, from_block=1, to_block=1)
        return read_chunks(cube["caller"], page).chunks[0].handle

    def intake(request):
        result = prepare_values(caller, request)
        mission = names[result.mission] if result.mission else None
        return mission, len(result.candidates)

    # With a silo, intake needs the cube rule for it; without one, the mission listing decides.
    in_budget = PrepareValues(handle=table(cube["documents"][a]), silo="budget")
    no_silo = PrepareValues(handle=table(paths["library/handbook.md"]), mission=a)
    catalog = list_documents(caller, DocumentList(limit=50)).documents
    hits = search(caller, LibrarySearch(query="peak power", kinds=["chunk"])).hits
    similar = SimilarMissions(key=a, facets=["design", "operations"])
    graph = RelationsRequest(item=cube["documents"][a])
    assert {
        "catalog": named(document.item for document in catalog),
        "search": named({hit.handle.item for hit in hits}),
        "find": {names[key]: named(ids) for key, ids in found(caller, above(300)).items()},
        "similar": answer(
            lambda: [
                (names[row.mission.key], row.facet_scores)
                for row in similar_missions(caller, similar).missions
            ]
        ),
        "intake": {
            "budget silo": answer(lambda: intake(in_budget)),
            "no silo": answer(lambda: intake(no_silo)),
        },
        "relations": answer(lambda: sorted(node(n.id) for n in relations(caller, graph).nodes)),
    } == EXPECTED[who]
