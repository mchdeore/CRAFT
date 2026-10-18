"""Cited facet lifecycle, stable rebuilds and one checked configured summary call."""

import json

from sqlalchemy import select

from app.cube.tests.test_similarity import pair as pair
from app.infrastructure.access import Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_owner_sessions
from app.library import facets
from app.library.handlers import index_provider
from app.tests.support.cube import cube_source as cube_source


def records(source):
    with source["owner"].connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(m.facet_texts)
                .where(m.facet_texts.c.mission_key.in_([source["mission"], source["other"]]))
                .order_by(m.facet_texts.c.mission_key, m.facet_texts.c.facet)
            ).mappings()
        ]


def test_validator(pair, monkeypatch):
    valid = pair["item"]
    bad = "itm_000000000000"
    text = "No source here. Valid fact. [" + valid + "] Foreign fact. [" + bad + "]"
    kept, sources = facets.validate(text, {valid})
    correct = kept == "Valid fact. [" + valid + "]" and sources == [valid]
    assert correct
    monkeypatch.setattr(
        facets, "draft", lambda caller, documents: {"design": "Uncited. [" + bad + "]"}
    )
    pair["provider"].write_content(pair["path"], b"# Simulated\n\nA revised simulated fact.\n")
    index_provider(pair["settings"])
    assert not any(row["mission_key"] == pair["mission"] for row in records(pair))


def test_fake_facets_deterministic(pair):
    sessions, engine = make_owner_sessions(pair["settings"])
    caller = Caller(settings=pair["settings"], sessions=sessions)
    try:
        inputs = {pair["mission"], pair["other"]}
        items = set(pair["provider"].items())
        facets.build(caller, inputs, items)
        first = [
            (row["mission_key"], row["facet"], row["text"], row["sources"]) for row in records(pair)
        ]
        facets.build(caller, inputs, items)
        second = [
            (row["mission_key"], row["facet"], row["text"], row["sources"]) for row in records(pair)
        ]
        identical = first == second
        assert identical and first
        assert index_provider(pair["settings"]) == {}
    finally:
        engine.dispose()


def test_facet_vectors(pair):
    rows = records(pair)
    with pair["owner"].connect() as connection:
        vectors = [
            dict(row)
            for row in connection.execute(
                select(m.vectors.c.owner_id, m.vectors.c.recipe).where(
                    m.vectors.c.owner_kind == "facet_text",
                    m.vectors.c.owner_id.in_([r["id"] for r in rows]),
                )
            ).mappings()
        ]
    assert len(vectors) == len(rows) == 2
    assert all(row["recipe"] == "facet-v1" for row in vectors)
    assert len({row["owner_id"] for row in vectors}) == len(rows)


def test_rebuild_on_change(pair):
    before = {row["mission_key"]: row["id"] for row in records(pair)}
    pair["provider"].write_content(
        pair["other_path"], b"# Simulated peer\n\nA revised peer uses 450 W.\n"
    )
    index_provider(pair["settings"])
    after = {row["mission_key"]: row["id"] for row in records(pair)}
    assert before[pair["mission"]] == after[pair["mission"]]
    assert before[pair["other"]] != after[pair["other"]]
    assert index_provider(pair["settings"]) == {}


def test_generated_summary_mode(cube_source, monkeypatch):
    import msgspec
    from agent_framework import ChatResponse, Message

    from app.agent import model
    from app.agent.fake import FakeChatClient
    from app.library import index
    from app.library.embed import embed

    calls = []

    def reply(self, messages):
        calls.append(True)
        text = messages[-1].text
        try:
            documents = json.loads(text)
        except ValueError:
            documents = None
        answer = (
            json.dumps({"design": "A simulated summary. [" + documents[0]["item"] + "]"})
            if isinstance(documents, list)
            else " ".join(["Simulated"] * 200)
        )
        return ChatResponse(
            messages=[Message(role="assistant", contents=[answer])], model="scripted"
        )

    monkeypatch.setattr(FakeChatClient, "reply", reply)
    monkeypatch.setattr(model, "client", lambda settings, role: FakeChatClient(role))
    monkeypatch.setattr(index, "embed", lambda text, caller=None: embed(text))
    monkeypatch.setattr(facets, "embed", lambda text, caller=None: embed(text))
    configured = msgspec.structs.replace(
        cube_source["settings"],
        model_main="synthetic-model",
        model_key="synthetic-key",
        model_endpoint="https://synthetic.invalid",
    )
    cube_source["provider"].write_content(
        cube_source["path"], b"# Simulated\n\nA new simulated source.\n"
    )
    index_provider(configured)
    with cube_source["owner"].connect() as connection:
        row = connection.execute(
            select(m.doc_versions.c.summary, m.doc_versions.c.summary_kind).where(
                m.doc_versions.c.item == cube_source["item"], m.doc_versions.c.version == 2
            )
        ).one()
    assert row.summary_kind == "generated" and len(row.summary.split()) == 150
    assert len(calls) == 2  # One document summary and one mission facet request.
