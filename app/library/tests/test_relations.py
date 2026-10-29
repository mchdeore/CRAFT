"""Document relations read the frozen rows as a graph, and show only what the caller may read."""

from dataclasses import replace
from uuid import uuid4

import msgspec
import pytest

from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import InvalidInput, NotFound, NotSignedIn
from app.library.handlers import Graph, RelationsRequest, relations
from app.tests.support import rows
from app.tests.support.cube import cube_source as cube_source


def edges(graph: Graph) -> set[tuple[str, str, str]]:
    return {(edge.from_node, edge.relation, edge.to_node) for edge in graph.edges}


def test_an_item_shows_its_versions_values_facets_and_mission(cube_source):
    source, item, mission = cube_source, cube_source["item"], cube_source["mission"]
    provider, owner = source["provider"], source["owner"]
    value = source["add"]()
    provider.write_content(source["path"], b"# 1 Budget\n\nRevised.\n")
    neighbour_path = f"missions/{mission}/engineering/st3-design/notes.md"
    provider.write_content(neighbour_path, b"# Notes\n\nA second document.\n")
    rows.load(owner, provider)
    neighbour = next(key for key, r in provider.items().items() if r["path"] == neighbour_path)
    with owner.begin() as connection:
        facet = connection.execute(
            m.facet_texts.insert()
            .values(mission_key=mission, facet="design", text="t", sources=[item], model="test")
            .returning(m.facet_texts.c.id)
        ).scalar_one()
        # A facet naming a source the caller cannot read stays hidden.
        connection.execute(
            m.facet_texts.insert().values(
                mission_key=mission,
                facet="purpose",
                text="t",
                sources=[item, "itm_000000000000"],
                model="test",
            )
        )

    graph = relations(source["caller"], RelationsRequest(item=item))

    assert edges(graph) == {
        (f"{item}@1", "version_of", item),
        (f"{item}@2", "version_of", item),
        (f"{item}@1", "cited_by_value", f"value:{value}"),
        (item, "facet_source", f"facet:{facet}"),
        (item, "same_mission", f"mission:{mission}"),
        (f"{neighbour}@1", "version_of", neighbour),
        (neighbour, "same_mission", f"mission:{mission}"),
    }
    kinds = {node.id: node.kind for node in graph.nodes}
    assert kinds[item] == kinds[neighbour] == "document"
    assert kinds[f"value:{value}"] == "value" and kinds[f"mission:{mission}"] == "mission"
    assert {node.label for node in graph.nodes if node.kind == "version"} == {"v1", "v2"}
    assert all(node.linked and not node.missing for node in graph.nodes)
    wire = msgspec.to_builtins(graph)
    assert set(wire["edges"][0]) == {"id", "from", "to", "relation"}

    by_mission = relations(source["caller"], RelationsRequest(mission=mission))
    assert edges(by_mission) == edges(graph)


def test_relations_follow_mission_grants(cube_source):
    item, mission = cube_source["item"], cube_source["mission"]
    cube_source["add"]()
    stranger = Caller(
        user_id=uuid4(),
        roles=frozenset({"member"}),
        sessions=cube_source["caller"].sessions,
        settings=cube_source["settings"],
    )
    with pytest.raises(NotFound):
        relations(stranger, RelationsRequest(item=item))
    assert relations(stranger, RelationsRequest(mission=mission)).nodes == []
    with pytest.raises(NotFound):
        relations(cube_source["caller"], RelationsRequest(item=item, mission="msn-other"))
    # An admin reads every document but, as for every cube read, sees values only with a grant.
    admin = replace(stranger, roles=frozenset({"admin"}))
    kinds = {node.kind for node in relations(admin, RelationsRequest(item=item)).nodes}
    assert kinds == {"document", "version", "mission"}


def test_relations_need_a_signed_in_caller_and_an_item_or_mission(cube_source):
    with pytest.raises(NotSignedIn):
        relations(Caller(), RelationsRequest(item=cube_source["item"]))
    with pytest.raises(InvalidInput):
        relations(cube_source["caller"], RelationsRequest())
    with pytest.raises(msgspec.ValidationError):
        msgspec.convert({"item": "not-an-item"}, RelationsRequest)
