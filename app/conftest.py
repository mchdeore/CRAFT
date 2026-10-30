"""Test setup: every test uses the test database, never craft_dev."""

from collections.abc import Iterator

import msgspec
import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import URL, Engine, create_engine, func, select

from app import create_app, load_operations
from app.accounts import service as accounts
from app.identity import service as identity
from app.infrastructure.config.env import read_env
from app.infrastructure.config.settings import Settings
from app.infrastructure.operations.storage import call_log
from app.infrastructure.paths import ROOT
from app.tests.support import model as scripted_model

# Tests read the operation registry directly, often before or without create_app.
load_operations()

# scripts/check gives each worktree its own; a plain pytest uses craft_test.
TEST_DATABASE = read_env().get("CRAFT_TEST_DATABASE") or "craft_test"
# The only .env keys tests take: how to reach the test database. Everything else keeps its
# default, so a developer's Hub, sign-in or model settings never reach a test.
DATABASE_KEYS = {"CRAFT_APP_DB_PASSWORD", "CRAFT_DATABASE_HOST", "CRAFT_DATABASE_PORT"}


@pytest.fixture(scope="session")
def settings() -> Settings:
    env = read_env()
    source = {key: env[key] for key in DATABASE_KEYS & env.keys()} | scripted_model.ENVIRONMENT
    return msgspec.structs.replace(
        Settings.from_env(source), database_name=TEST_DATABASE, local_password_signin=True
    )


@pytest.fixture(scope="session", autouse=True)
def scripted() -> Iterator[None]:
    """Tests answer from the scripted model, never the configured one, and hash passwords
    cheaply. Tests that store a password hash it with `accounts.PASSWORDS`."""
    cheap = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    with pytest.MonkeyPatch.context() as patch:
        scripted_model.install(patch)
        patch.setattr(accounts, "PASSWORDS", cheap)
        patch.setattr(identity, "PASSWORDS", cheap)
        yield


@pytest.fixture(scope="session", autouse=True)
def migrated(settings: Settings) -> None:
    """Upgrade the test database to head once per session (no revisions until S1)."""
    config = Config(toml_file=str(ROOT / "pyproject.toml"))
    config.attributes["database_name"] = TEST_DATABASE
    command.upgrade(config, "head")


@pytest.fixture
def app(settings: Settings) -> Iterator[FastAPI]:
    app = create_app(settings)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def owner_engine(settings: Settings) -> Iterator[Engine]:
    """craft_owner on the test database: sets up tables a test needs (S0 has no migrations)."""
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


@pytest.fixture
def last_call_id(owner_engine: Engine) -> int:
    """The newest call_log id before the test: rows above it are the test's own."""
    with owner_engine.connect() as connection:
        return connection.execute(select(func.max(call_log.c.id))).scalar_one() or 0
