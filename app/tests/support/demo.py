"""A generated corpus in a dedicated disposable database."""

import json
import os
import subprocess
from uuid import uuid4

import msgspec
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.paths import ROOT
from app.seed.demo import seed


@pytest.fixture(scope="module")
def demo(tmp_path_factory, settings):
    project = "craft-demo-" + uuid4().hex[:8]
    environment = os.environ | {
        "CRAFT_DATABASE_PORT": "0",
        "CRAFT_DATABASE_NAME": "craft_test",
        "CRAFT_MODEL_MAIN": "fake",
        "CRAFT_MODEL_KEY": "",
        "CRAFT_MODEL_ENDPOINT": "",
        "CRAFT_DB_ADMIN_PASSWORD": os.environ.get("CRAFT_DB_ADMIN_PASSWORD") or uuid4().hex,
    }
    compose = ["docker", "compose", "-p", project]
    started = subprocess.run(
        [*compose, "up", "-d", "--wait", "db"], env=environment, capture_output=True
    )
    engine = None
    try:
        assert started.returncode == 0
        binding = subprocess.run(
            [*compose, "port", "db", "5432"], env=environment, capture_output=True, text=True
        )
        assert binding.returncode == 0
        port = int(binding.stdout.strip().rsplit(":", 1)[1])
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
