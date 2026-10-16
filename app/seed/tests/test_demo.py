"""Real source bytes, provider history and indexed facts in an owned disposable database."""

import hashlib
import json
import os
import socket
import subprocess
from datetime import date
from pathlib import Path
from uuid import uuid4

import msgspec
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.paths import ROOT
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library.handlers import convert_source as convert
from app.seed.demo import generate, seed


def file_hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture(scope="module")
def demo(tmp_path_factory, settings):
    project = "craft-demo-" + uuid4().hex[:8]
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = os.environ | {
        "CRAFT_DATABASE_PORT": str(port),
        "CRAFT_DATABASE_NAME": "craft_test",
    }
    compose = ["docker", "compose", "-p", project]
    started = subprocess.run(
        [*compose, "up", "-d", "--wait", "db"], env=environment, capture_output=True
    )
    assert started.returncode == 0
    engine = None
    try:
        one = tmp_path_factory.mktemp("demo-one")
        two = tmp_path_factory.mktemp("demo-two")
        configured = msgspec.structs.replace(settings, database_port=port, provider_root=str(one))
        sessions, engine = make_owner_sessions(configured)
        with engine.begin() as connection:
            config = Config(str(ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            config.attributes["database_name"] = "craft_test"
            command.upgrade(config, "head")
        seed(configured, 1)
        with sessions.begin() as session:
            before = session.execute(select(m.cube_values).order_by(m.cube_values.c.id)).all()
        seed(msgspec.structs.replace(configured, provider_root=str(two)), 1)
        with sessions.begin() as session:
            after = session.execute(select(m.cube_values).order_by(m.cube_values.c.id)).all()
        stable = before == after
        yield {
            "one": one,
            "two": two,
            "settings": configured,
            "sessions": sessions,
            "truth": json.loads((one / ".provider/ground-truth.json").read_text()),
            "stableValues": stable,
        }
    finally:
        if engine is not None:
            engine.dispose()
        stopped = subprocess.run(
            [*compose, "down", "--volumes"], env=environment, capture_output=True
        )
        assert stopped.returncode == 0


def test_same_seed_same_bytes(demo) -> None:
    identical = file_hashes(demo["one"]) == file_hashes(demo["two"])
    assert identical and demo["stableValues"]
    previous = file_hashes(demo["one"])
    seed(demo["settings"], 1)
    unchanged = previous == file_hashes(demo["one"])
    assert unchanged


def test_other_seed_differs(demo, tmp_path) -> None:
    generate(tmp_path, 2)
    first = file_hashes(demo["one"])
    second = file_hashes(tmp_path)
    changed = sum(second.get(path) != digest for path, digest in first.items())
    assert changed >= len(first) / 2


def test_counts(demo) -> None:
    truth = demo["truth"]
    assert len(truth["missions"]) == 12 and len(truth["families"]) == 4
    assert all(len(family["missions"]) == 3 for family in truth["families"])
    assert 100 <= truth["documents"] <= 140
    formats = {Path(row["path"]).suffix for row in SimulatedProvider(demo["one"]).items().values()}
    assert {".md", ".txt", ".html", ".docx", ".pdf", ".pptx", ".xlsx", ".csv"} <= formats
    assert len(truth["questions"]) >= 48


def test_every_question_answerable(demo) -> None:
    with demo["sessions"].begin() as session:
        for question in demo["truth"]["questions"]:
            for citation in question["answers"]:
                row = session.execute(
                    select(m.chunks.c.text, m.chunks.c.text_sha256, m.chunks.c.locator)
                    .join(m.doc_versions)
                    .where(
                        m.doc_versions.c.item == citation["item"],
                        m.doc_versions.c.version == citation["version"],
                        m.chunks.c.block == citation["block"],
                    )
                ).one()
                hash_matches = row.text_sha256.hex() == citation["quoteSha256"]
                locator_matches = row.locator == citation["locator"]
                fact_present = question["expectedText"] in " ".join(row.text.split())
                assert hash_matches and locator_matches and fact_present


def test_values_and_state(demo) -> None:
    with demo["sessions"].begin() as session:
        for value in demo["truth"]["values"]:
            row = session.execute(
                select(m.cube_values, m.provider_items.c.deleted_at)
                .join(m.provider_items, m.provider_items.c.item == m.cube_values.c.item)
                .where(
                    m.cube_values.c.mission_key == value["mission"],
                    m.cube_values.c.parameter == value["parameter"],
                    m.cube_values.c.silo == value["silo"],
                    m.cube_values.c.stage == value["stage"],
                    m.cube_values.c.element_id.is_(None),
                    m.cube_values.c.valid_from <= date.fromisoformat(value["date"]),
                )
                .order_by(
                    m.cube_values.c.valid_from.desc(),
                    m.cube_values.c.recorded_at.desc(),
                    m.cube_values.c.id.desc(),
                )
                .limit(1)
            ).one()
            withdrawn = row.deleted_at is not None
            kind = "unknown" if withdrawn else row.kind
            low = None if withdrawn or row.low is None else float(row.low)
            high = None if withdrawn or row.high is None else float(row.high)
            matched = (
                withdrawn == value["withdrawn"]
                and kind == value["kind"]
                and low == value["low"]
                and high == value["high"]
                and row.source_unit == value["sourceUnit"]
            )
            assert matched
            hash_matches = row.quote_sha256.hex() == value["citation"]["quoteSha256"]
            assert hash_matches


def test_changes_planted(demo) -> None:
    provider = SimulatedProvider(demo["one"])
    changes, _ = provider.list_changes(0)
    kinds = {(row["item"], row["kind"]) for row in changes}
    truth = demo["truth"]
    assert len(truth["versions"]) == 12 and len(truth["moves"]) == 2
    assert len(truth["deletes"]) == 3 and len(truth["duplicates"]) == 3
    assert all((row["item"], "changed") in kinds for row in truth["versions"])
    assert all((row["item"], "moved") in kinds for row in truth["moves"])
    assert all((row["item"], "deleted") in kinds for row in truth["deletes"])
    for duplicate in truth["duplicates"]:
        a = provider.get_item(duplicate["item"])
        b = provider.get_item(duplicate["original"])
        assert a["versions"][-1]["sha256"] != b["versions"][-1]["sha256"]
        chunks_a = convert(
            provider.read_content(a["item"], a["versions"][-1]["version"]), a["path"]
        )
        chunks_b = convert(
            provider.read_content(b["item"], b["versions"][-1]["version"]), b["path"]
        )
        equal_cells = [x.text for x in chunks_a] == [x.text for x in chunks_b]
        assert equal_cells


def test_labels(demo) -> None:
    assert all(row["simulated"] for row in SimulatedProvider(demo["one"]).items().values())
    with demo["sessions"].begin() as session:
        assert not session.execute(
            select(m.missions.c.key).where(m.missions.c.simulated.is_(False))
        ).first()
        assert not session.execute(
            select(m.cube_values.c.id).where(m.cube_values.c.simulated.is_(False))
        ).first()


def test_content_check_clean(demo, tmp_path) -> None:
    configured = os.environ.get("CONTENT_CHECK_SCRIPT")
    if not configured:
        pytest.skip("Optional text audit script is not configured in this environment.")
    checker = Path(configured)
    if not checker.exists():
        pytest.skip("Optional text audit script is unavailable in this environment.")
    provider = SimulatedProvider(demo["one"])
    dump = tmp_path / "simulated-text.txt"
    with dump.open("w") as stream:
        for item in provider.items().values():
            for version in item["versions"]:
                for block in convert(
                    provider.read_content(item["item"], version["version"]), item["path"]
                ):
                    stream.write(block.text + "\n")
    result = subprocess.run(
        ["python3", str(checker), "--counts", "--text", str(dump)], capture_output=True
    )
    assert result.returncode == 0
