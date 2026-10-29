"""Document relations read the frozen rows as a graph, and show only what the caller may read."""

from dataclasses import replace
from uuid import uuid4

import msgspec
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import InvalidInput, NotFound, NotSignedIn
from app.infrastructure.operations.storage import call_log
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


def test_an_unknown_item_is_not_found_and_an_unknown_mission_is_empty(cube_source):
    caller = cube_source["caller"]
    with pytest.raises(NotFound):
        relations(caller, RelationsRequest(item="itm_ffffffffffff"))
    assert relations(caller, RelationsRequest(mission="msn-none")) == Graph(
        nodes=[], edges=[], groups=[]
    )


@pytest.fixture
def silo_member(cube_source):
    """A member whose only grant is one silo of the synthetic mission; the silo is set per test."""
    users, roles = m.metadata.tables["users"], m.metadata.tables["user_roles"]
    with cube_source["owner"].begin() as connection:
        user = connection.execute(
            users.insert()
            .values(username="silo_" + uuid4().hex, display_name="Silo member")
            .returning(users.c.id)
        ).scalar_one()
        connection.execute(roles.insert().values(user_id=user, role="member"))

    def grant(silo: str):
        with cube_source["owner"].begin() as connection:
            connection.execute(m.mission_grants.delete().where(m.mission_grants.c.user_id == user))
            connection.execute(
                m.mission_grants.insert().values(
                    user_id=user, mission_key=cube_source["mission"], silo=silo
                )
            )
        return replace(cube_source["caller"], user_id=user)

    yield grant
    with cube_source["owner"].begin() as connection:
        connection.execute(m.mission_grants.delete().where(m.mission_grants.c.user_id == user))
        connection.execute(roles.delete().where(roles.c.user_id == user))
        connection.execute(users.delete().where(users.c.id == user))


def test_a_silo_grant_shows_only_that_silos_documents_and_values(cube_source, silo_member):
    item, mission = cube_source["item"], cube_source["mission"]
    engineering = cube_source["add"]()
    budget = cube_source["add"]()
    with cube_source["owner"].begin() as connection:
        connection.execute(
            m.cube_values.update().where(m.cube_values.c.id == budget).values(silo="budget")
        )

    outsider = silo_member("budget")
    with pytest.raises(NotFound):
        relations(outsider, RelationsRequest(item=item))
    assert relations(outsider, RelationsRequest(mission=mission)).nodes == []

    insider = silo_member("engineering")
    graph = relations(insider, RelationsRequest(item=item))
    values = {node.id for node in graph.nodes if node.kind == "value"}
    assert values == {f"value:{engineering}"}
    assert (f"{item}@1", "cited_by_value", f"value:{budget}") not in edges(graph)


def test_the_route_checks_access_and_logs_the_call(cube_source):
    item = cube_source["item"]
    with TestClient(cube_source["app"], base_url="https://testserver") as http:
        assert http.get("/api/library/relations", params={"item": item}).status_code == 401
        assert cube_source["sign_in"](http).status_code == 200
        response = http.get("/api/library/relations", params={"item": item})
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"nodes", "edges", "groups"}
        assert {node["id"] for node in body["nodes"]} >= {item, f"{item}@1"}
        assert http.get("/api/library/relations").status_code == 400
        assert http.get("/api/library/relations", params={"item": "x"}).status_code == 400
        missing = {"item": "itm_ffffffffffff"}
        assert http.get("/api/library/relations", params=missing).status_code == 404
    with cube_source["owner"].begin() as connection:
        outcomes = connection.execute(
            select(call_log.c.outcome, call_log.c.error_code)
            .where(
                call_log.c.name == "library.relations",
                call_log.c.user_id == cube_source["caller"].user_id,
            )
            .order_by(call_log.c.id)
        ).all()
    assert [outcome for outcome, _ in outcomes] == ["ok", "error", "error", "error"]
