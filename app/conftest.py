"""Test setup: every test uses the craft_test database, never craft_dev."""

from collections.abc import Iterator

import msgspec
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import URL, Engine, create_engine

from app import create_app
from app.infrastructure.config.env import read_env
from app.infrastructure.config.settings import Settings
from app.infrastructure.paths import ROOT

TEST_DATABASE = "craft_test"


@pytest.fixture(scope="session")
def settings() -> Settings:
    source = read_env() | {
        "CRAFT_MODEL_MAIN": "fake",
        "CRAFT_MODEL_KEY": "",
        "CRAFT_MODEL_ENDPOINT": "",
    }
    return msgspec.structs.replace(Settings.from_env(source), database_name=TEST_DATABASE)


@pytest.fixture(scope="session", autouse=True)
def migrated(settings: Settings) -> None:
    """Upgrade craft_test to head once per session (no revisions until S1)."""
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_name"] = TEST_DATABASE
    command.upgrade(config, "head")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def owner_engine(settings: Settings) -> Iterator[Engine]:
    """craft_owner on craft_test: sets up tables a test needs (S0 has no migrations)."""
    url = URL.create(
        "postgresql+psycopg",
        username="craft_owner",
        password=read_env()["CRAFT_OWNER_DB_PASSWORD"],
        host=settings.database_host,
        port=settings.database_port,
        database=TEST_DATABASE,
    )
    engine = create_engine(url)
    yield engine
    engine.dispose()
