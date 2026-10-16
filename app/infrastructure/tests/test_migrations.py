"""Migration verification rolls back DDL in craft_test, preserving other fixtures."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, text

from app.infrastructure.database.metadata import metadata
from app.infrastructure.paths import ROOT


@pytest.fixture
def isolated_migrations(owner_engine: Engine) -> Iterator[tuple[Config, Connection]]:
    with owner_engine.connect() as connection, connection.begin() as transaction:
        assert connection.execute(text("select current_database()")).scalar() == "craft_test"
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["database_name"] = "craft_test"
        config.attributes["connection"] = connection
        yield config, connection
        transaction.rollback()


def assert_models(connection: Connection) -> None:
    context = MigrationContext.configure(connection)
    assert compare_metadata(context, metadata) == []


def test_upgrade_on_empty_database(isolated_migrations: tuple[Config, Connection]) -> None:
    config, connection = isolated_migrations
    command.downgrade(config, "base")
    assert not set(metadata.tables).intersection(inspect(connection).get_table_names())
    command.upgrade(config, "head")
    expected = ScriptDirectory.from_config(config).get_current_head()
    assert connection.execute(text("select version_num from alembic_version")).scalar() == expected
    assert set(metadata.tables) <= set(inspect(connection).get_table_names())
    assert {"users", "local_credentials", "user_roles", "auth_sessions", "call_log"} <= set(
        inspect(connection).get_table_names()
    )
    assert_models(connection)


def test_accounts_call_log_migration_roundtrip(
    isolated_migrations: tuple[Config, Connection],
) -> None:
    config, connection = isolated_migrations
    command.downgrade(config, "base")
    command.upgrade(config, "0002")
    inspector = inspect(connection)
    assert len(inspector.get_indexes("call_log")) == 4
    assert len(inspector.get_check_constraints("call_log")) == 3
    assert inspector.get_foreign_keys("call_log") == []
    command.upgrade(config, "head")
    assert_models(connection)


def test_conversation_run_approval_roundtrip(
    isolated_migrations: tuple[Config, Connection],
) -> None:
    config, connection = isolated_migrations
    command.downgrade(config, "0002")
    assert "approval_requests" not in inspect(connection).get_table_names()
    command.upgrade(config, "0005")
    assert "approval_status" in inspect(connection).get_view_names()
    command.upgrade(config, "head")
    assert_models(connection)


def test_migration_round_trip(isolated_migrations: tuple[Config, Connection]) -> None:
    config, connection = isolated_migrations
    command.downgrade(config, "base")
    command.upgrade(config, "0008")
    assert "workspace_files" in inspect(connection).get_table_names()
    command.upgrade(config, "head")
    assert_models(connection)
