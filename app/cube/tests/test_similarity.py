"""Real source permissions, exact facet contributions and deterministic synthetic families."""

import math
from dataclasses import replace
from uuid import uuid4

import msgspec
import pytest
from sqlalchemy import select

from app.cube.handlers import (
    ExplainSimilarity,
    SimilarMissions,
    explain_similarity,
    similar_missions,
)
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import InvalidInput, NotFound
from app.library import handlers as library
from app.seed.tests.test_demo import demo as demo


@pytest.fixture
def pair(cube_source):
    source = cube_source
    other = "msn-peer-" + uuid4().hex[:16]
    third = "msn-comparator-" + uuid4().hex[:16]
    extra = []
    for key, label in [(other, "peer"), (third, "comparator")]:
        path = f"missions/{key}/engineering/st3-design/{label}.md"
        with source["owner"].begin() as connection:
            connection.execute(
                m.missions.insert().values(
                    key=key,
                    title="Simulated " + label,
                    current_stage="st3-design",
                    simulated=True,
                )
            )
            connection.execute(
                m.mission_grants.insert().values(
                    user_id=source["caller"].user_id,
                    mission_key=key,
                    silo=None,
                )
            )
        source["provider"].write_content(
            path, ("# Simulated\n\nA simulated " + label + " uses 400 W.\n").encode()
        )
        item = next(
            row["item"] for row in source["provider"].items().values() if row["path"] == path
        )
        extra.append((key, path, item))
    library.index_provider(source["settings"])
    yield source | {
        "other": other,
        "other_path": extra[0][1],
        "other_item": extra[0][2],
        "third": third,
        "third_item": extra[1][2],
    }
    with source["owner"].begin() as connection:
        for key, _path, item in extra:
            ids = list(
                connection.execute(
                    select(m.facet_texts.c.id).where(m.facet_texts.c.mission_key == key)
                ).scalars()
            )
            docs = list(
                connection.execute(
                    select(m.doc_versions.c.id).where(m.doc_versions.c.item == item)
                ).scalars()
            )
            chunks = list(
                connection.execute(
                    select(m.chunks.c.id).where(m.chunks.c.doc_version_id.in_(docs))
                ).scalars()
            )
            connection.execute(
                m.vectors.delete().where(m.vectors.c.owner_id.in_([*ids, *docs, *chunks]))
            )
            connection.execute(m.facet_texts.delete().where(m.facet_texts.c.mission_key == key))
            connection.execute(m.doc_versions.delete().where(m.doc_versions.c.item == item))
            connection.execute(m.provider_items.delete().where(m.provider_items.c.item == item))
            connection.execute(
                m.mission_grants.delete().where(m.mission_grants.c.mission_key == key)
            )
            connection.execute(m.missions.delete().where(m.missions.c.key == key))


def set_facets(source, mission, facets, item, first=1.0, second=0.0):
    with source["owner"].begin() as connection:
        ids = list(
            connection.execute(
                select(m.facet_texts.c.id).where(m.facet_texts.c.mission_key == mission)
            ).scalars()
        )
        connection.execute(m.vectors.delete().where(m.vectors.c.owner_id.in_(ids)))
        connection.execute(m.facet_texts.delete().where(m.facet_texts.c.mission_key == mission))
        for facet in facets:
            identifier = connection.execute(
                m.facet_texts.insert()
                .values(
                    mission_key=mission,
                    facet=facet,
                    text="A simulated cited fact. [" + item + "]",
                    sources=[item],
                    model="fake",
                )
                .returning(m.facet_texts.c.id)
            ).scalar_one()
            connection.execute(
                m.vectors.insert().values(
                    owner_kind="facet_text",
                    owner_id=identifier,
                    recipe="facet-v1",
                    model="fake-embed@1",
                    embedding=[first, second] + [0.0] * 1534,
                )
            )


def test_not_comparable(pair):
    facets: list[library.Facet] = ["purpose", "design", "operations", "program"]
    set_facets(pair, pair["mission"], facets, pair["item"])
    set_facets(pair, pair["other"], ["design"], pair["other_item"])
    set_facets(pair, pair["third"], facets, pair["third_item"])
    result = similar_missions(pair["caller"], SimilarMissions(key=pair["mission"], facets=facets))
    row = next(row for row in result.missions if row.mission.key == pair["other"])
    assert not row.comparable and row.score is None and row.facet_scores["design"] == 1
    assert result.missions[0].comparable and result.missions[0].mission.key == pair["third"]
    assert result.missions[-1].mission.key == pair["other"]
    assert sum(value is not None for value in row.facet_scores.values()) == 1


def test_access_on_sources(pair):
    set_facets(pair, pair["mission"], ["design"], pair["item"])
    set_facets(pair, pair["other"], ["design"], pair["other_item"])
    # Revoke only the peer source silo while keeping its mission visible.
    with pair["owner"].begin() as connection:
        connection.execute(
            m.mission_grants.update()
            .where(
                m.mission_grants.c.user_id == pair["caller"].user_id,
                m.mission_grants.c.mission_key == pair["other"],
            )
            .values(silo="quality")
        )
    result = explain_similarity(
        pair["caller"],
        ExplainSimilarity(
            key=pair["mission"],
            other=pair["other"],
            facets=["design"],
        ),
    ).facets[0]
    safe = result.other_text is None and not result.other_sources and result.score is None
    assert safe and result.text is not None
    ranked = similar_missions(
        pair["caller"], SimilarMissions(key=pair["mission"], facets=["design"])
    )
    assert all(not row.comparable for row in ranked.missions if row.mission.key == pair["other"])


def test_explain(pair):
    set_facets(pair, pair["mission"], ["design"], pair["item"])
    set_facets(pair, pair["other"], ["design"], pair["other_item"], 0.6, 0.8)
    result = explain_similarity(
        pair["caller"],
        ExplainSimilarity(
            key=pair["mission"],
            other=pair["other"],
            facets=["design", "operations"],
        ),
    )
    design, missing = result.facets
    assert design.score is not None and math.isclose(design.score, 0.6, abs_tol=1e-7)
    assert design.text and design.other_text
    assert design.sources == [pair["item"]] and design.other_sources == [pair["other_item"]]
    assert missing.score is None and missing.text is None and missing.other_text is None
    assert not missing.sources and not missing.other_sources


def test_stale_vector_and_live_withdrawal(pair):
    set_facets(pair, pair["mission"], ["design"], pair["item"])
    set_facets(pair, pair["other"], ["design"], pair["other_item"])
    with pair["owner"].begin() as connection:
        identifier = connection.execute(
            select(m.facet_texts.c.id).where(
                m.facet_texts.c.mission_key == pair["other"],
                m.facet_texts.c.facet == "design",
            )
        ).scalar_one()
        connection.execute(
            m.vectors.update()
            .where(m.vectors.c.owner_id == identifier)
            .values(model="stale-embedding")
        )
    query = ExplainSimilarity(key=pair["mission"], other=pair["other"], facets=["design"])
    assert explain_similarity(pair["caller"], query).facets[0].score is None
    pair["provider"].delete_item(pair["other_item"])
    visible = explain_similarity(pair["caller"], query).facets[0]
    safe = visible.other_text is None and not visible.other_sources
    assert safe


def test_limits_defaults_and_cube_admin_grants(pair):
    result = similar_missions(pair["caller"], SimilarMissions(key=pair["mission"]))
    assert len(result.facets) == 5
    for facets in [[], ["design", "design"], ["unknown"]]:
        with pytest.raises(InvalidInput):
            similar_missions(pair["caller"], {"key": pair["mission"], "facets": facets})
    for limit in [0, 11]:
        with pytest.raises(InvalidInput):
            similar_missions(pair["caller"], {"key": pair["mission"], "limit": limit})
    admin = replace(pair["caller"], roles=frozenset({"admin"}))
    with pair["owner"].begin() as connection:
        connection.execute(
            m.mission_grants.delete().where(
                m.mission_grants.c.user_id == admin.user_id,
                m.mission_grants.c.mission_key == pair["other"],
            )
        )
    with pytest.raises(NotFound):
        explain_similarity(admin, ExplainSimilarity(key=pair["mission"], other=pair["other"]))


def test_families(demo):
    from app.infrastructure.mcp.stdio import local_caller

    caller = local_caller("demo", demo["settings"])
    families = {
        mission: family["key"]
        for family in demo["truth"]["families"]
        for mission in family["missions"]
    }
    checked = 0
    for mission in families:
        result = similar_missions(caller, SimilarMissions(key=mission, limit=10))
        comparable = [row for row in result.missions if row.comparable]
        matched = bool(comparable) and families[comparable[0].mission.key] == families[mission]
        assert matched
        checked += 1
    assert checked == 12


def test_mean_counts_only_available_facets(pair):
    facets: list[library.Facet] = ["design", "operations"]
    set_facets(pair, pair["mission"], facets, pair["item"])
    set_facets(pair, pair["other"], facets, pair["other_item"])
    with pair["owner"].begin() as connection:
        identifier = connection.execute(
            select(m.facet_texts.c.id).where(
                m.facet_texts.c.mission_key == pair["other"],
                m.facet_texts.c.facet == "operations",
            )
        ).scalar_one()
        connection.execute(
            m.vectors.update()
            .where(m.vectors.c.owner_id == identifier)
            .values(embedding=[0.0, 1.0] + [0.0] * 1534)
        )
    result = similar_missions(pair["caller"], SimilarMissions(key=pair["mission"], facets=facets))
    row = next(row for row in result.missions if row.mission.key == pair["other"])
    assert row.comparable and row.score == 0.5
    assert row.facet_scores == {"design": 1.0, "operations": 0.0}


def test_http_and_contract(pair):
    import json
    from pathlib import Path

    import jsonschema
    from fastapi.testclient import TestClient

    from app.cube.handlers import SimilarityExplanation, SimilarPage
    from app.tests.test_contract import root_schema, typescript_schema

    def valid(value, schema):
        try:
            jsonschema.validate(value, schema)
        except jsonschema.ValidationError:
            return False
        return True

    for name, shape in [
        ("similar-missions", SimilarPage),
        ("similarity-explanation", SimilarityExplanation),
    ]:
        example = Path("api/examples/" + name + ".json").read_bytes()
        msgspec.json.decode(example, type=shape)
        assert valid(json.loads(example), typescript_schema(shape.__name__))

    set_facets(pair, pair["mission"], ["design"], pair["item"])
    set_facets(pair, pair["other"], ["design"], pair["other_item"])
    with TestClient(pair["app"], base_url="https://testserver") as client:
        assert client.post("/api/cube/similar", json={"key": pair["mission"]}).status_code == 401
        assert pair["sign_in"](client).status_code == 200
        assert client.post("/api/cube/similar", json={"key": pair["mission"]}).status_code == 403
        headers = {"Origin": "https://testserver"}
        response = client.post(
            "/api/cube/similar",
            json={"key": pair["mission"], "facets": ["design"]},
            headers=headers,
        )
        assert response.status_code == 200
        assert valid(response.json(), typescript_schema("SimilarPage"))
        explanation = client.post(
            "/api/cube/similar/explain",
            json={"key": pair["mission"], "other": pair["other"], "facets": ["design"]},
            headers=headers,
        )
        assert explanation.status_code == 200
        assert valid(explanation.json(), typescript_schema("SimilarityExplanation"))
    for shape in [SimilarMissions, SimilarPage, ExplainSimilarity, SimilarityExplanation]:
        declared = root_schema(typescript_schema(shape.__name__))
        native = root_schema(msgspec.json.schema(shape))
        assert set(declared["properties"]) == set(native["properties"])
        assert set(declared["required"]) == set(native["required"])
