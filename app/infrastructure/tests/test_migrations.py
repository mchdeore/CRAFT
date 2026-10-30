"""Migration verification rolls back DDL in the test database, preserving other fixtures."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.infrastructure.config.settings import Settings
from app.infrastructure.database.metadata import (
    doc_versions,
    include_name,
    metadata,
    provider_items,
)
from app.infrastructure.paths import ROOT


@pytest.fixture
def isolated_migrations(
    owner_engine: Engine, settings: Settings
) -> Iterator[tuple[Config, Connection]]:
    name = settings.database_name
    with owner_engine.connect() as connection, connection.begin() as transaction:
        assert connection.execute(text("select current_database()")).scalar() == name
        config = Config(toml_file=str(ROOT / "pyproject.toml"))
        config.attributes["database_name"] = name
        config.attributes["connection"] = connection
        yield config, connection
        transaction.rollback()


def assert_models(connection: Connection) -> None:
    context = MigrationContext.configure(connection, opts={"include_name": include_name})
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


@pytest.mark.parametrize("legacy_revision", ["0016", "0017"])
def test_upgrade_preserves_legacy_ocr_revision(
    isolated_migrations: tuple[Config, Connection],
    legacy_revision: str,
) -> None:
    config, connection = isolated_migrations
    command.downgrade(config, "0015")
    command.upgrade(config, "0016")
    connection.execute(
        text("alter table conversations drop constraint ck_conversations_group_name")
    )
    connection.execute(text("alter table conversations drop column group_name"))
    connection.execute(
        text(
            "alter table doc_versions add column ocr_text text, add column ocr_model text, "
            "add column ocr_updated_at timestamptz"
        )
    )
    item = "itm_000000000017"
    connection.execute(
        provider_items.insert().values(
            item=item,
            path="generated/legacy.pdf",
            title="Generated migration document",
            simulated=True,
            classification="unclassified",
        )
    )
    connection.execute(
        doc_versions.insert().values(
            item=item,
            version=1,
            sha256=b"\x01" * 32,
            media_type="application/pdf",
            summary="Generated legacy summary",
            summary_kind="extract",
            ocr_text="Generated existing OCR",
            ocr_model="generated-model",
        )
    )
    command.stamp(config, legacy_revision)
    command.upgrade(config, "head")
    assert connection.execute(
        select(doc_versions.c.ocr_text, doc_versions.c.ocr_model).where(doc_versions.c.item == item)
    ).one() == ("Generated existing OCR", "generated-model")
    assert "ck_conversations_group_name" in {
        constraint["name"]
        for constraint in inspect(connection).get_check_constraints("conversations")
    }
    assert_models(connection)


def test_upgrade_recovers_legacy_conversation_grouping(
    isolated_migrations: tuple[Config, Connection],
) -> None:
    config, connection = isolated_migrations
    command.downgrade(config, "0015")
    command.upgrade(config, "0016")
    connection.execute(
        text("alter table conversations drop constraint ck_conversations_group_name")
    )
    connection.execute(text("alter table conversations drop column group_name"))
    command.upgrade(config, "head")
    assert "group_name" in {
        column["name"] for column in inspect(connection).get_columns("conversations")
    }
    assert "ck_conversations_group_name" in {
        constraint["name"]
        for constraint in inspect(connection).get_check_constraints("conversations")
    }
    assert_models(connection)
    command.downgrade(config, "0016")
    assert "group_name" in {
        column["name"] for column in inspect(connection).get_columns("conversations")
    }


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


def test_reasoning_messages_survive_upgrade_and_incompatible_downgrade(
    isolated_migrations: tuple[Config, Connection],
) -> None:
    config, connection = isolated_migrations
    command.downgrade(config, "0013")
    owner = connection.execute(
        text(
            "insert into users (username, display_name) "
            "values ('generated-reasoning', 'Generated') returning id"
        )
    ).scalar_one()
    conversation = connection.execute(
        text(
            "insert into conversations (owner_id, title) values (:owner, 'Generated') returning id"
        ),
        {"owner": owner},
    ).scalar_one()
    statement = text(
        "insert into messages (conversation_id, seq, role, content) "
        "values (:conversation, :seq, :role, '{}'::jsonb) returning id"
    )
    ordinary = connection.execute(
        statement, {"conversation": conversation, "seq": 1, "role": "assistant"}
    ).scalar_one()
    command.upgrade(config, "0014")
    reasoning = connection.execute(
        statement, {"conversation": conversation, "seq": 2, "role": "reasoning"}
    ).scalar_one()
    with pytest.raises(IntegrityError), connection.begin_nested():
        command.downgrade(config, "0013")
    assert set(
        connection.execute(
            text("select id from messages where conversation_id = :conversation"),
            {"conversation": conversation},
        ).scalars()
    ) == {ordinary, reasoning}
    assert (
        connection.execute(text("select version_num from alembic_version")).scalar_one() == "0014"
    )


def test_workspace_call_rows_survive_an_incompatible_downgrade(
    isolated_migrations: tuple[Config, Connection],
) -> None:
    config, connection = isolated_migrations
    command.upgrade(config, "head")
    expected = ScriptDirectory.from_config(config).get_current_head()
    connection.execute(
        text(
            "insert into call_log (kind, name, origin, outcome, duration_ms, request_id, call_id) "
            "values ('operation', 'accounts.me', 'workspace', 'ok', 1, uuidv7(), uuidv7())"
        )
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        command.downgrade(config, "0014")
    assert (
        connection.execute(
            text("select count(*) from call_log where origin = 'workspace'")
        ).scalar_one()
        == 1
    )
    assert (
        connection.execute(text("select version_num from alembic_version")).scalar_one() == expected
    )


def test_existing_passwords_move_to_craft_id_and_users_stay_linked(
    isolated_migrations: tuple[Config, Connection],
) -> None:
    config, connection = isolated_migrations
    command.downgrade(config, "0021")
    user = connection.execute(
        text("insert into users (username, display_name) values ('moved', 'Moved') returning id")
    ).scalar_one()
    connection.execute(
        text("insert into local_credentials (user_id, password_hash) values (:u, 'hash')"),
        {"u": user},
    )
    command.upgrade(config, "head")
    moved = connection.execute(
        text(
            "select a.id, a.username, c.password_hash from id_accounts a "
            "join id_credentials c on c.account_id = a.id where a.id = :u"
        ),
        {"u": user},
    ).one()
    assert tuple(moved) == (user, "moved", "hash")
    linked = connection.execute(
        text("select idp_object_id from users where id = :u"), {"u": user}
    ).scalar_one()
    assert linked == user


def test_token_kinds_upgrade_as_workspace_and_downgrade_revokes_mcp_tokens(
    isolated_migrations: tuple[Config, Connection],
) -> None:
    config, connection = isolated_migrations
    command.downgrade(config, "0030")
    user = connection.execute(
        text("insert into users (username, display_name) values ('kinds', 'Kinds') returning id")
    ).scalar_one()
    insert = (
        "insert into workspace_tokens (user_id, token_sha256, label, expires_at{kind}) "
        "values (:u, :digest, 'generated', now() + interval '{hours} hours'{value}) returning id"
    )
    earlier = connection.execute(
        text(insert.format(kind="", hours=168, value="")), {"u": user, "digest": b"e" * 32}
    ).scalar_one()
    command.upgrade(config, "0031")
    assert (
        connection.execute(
            text("select kind from workspace_tokens where id = :id"), {"id": earlier}
        ).scalar_one()
        == "workspace"
    )
    mcp = connection.execute(
        text(insert.format(kind=", kind", hours=720, value=", 'mcp'")),
        {"u": user, "digest": b"m" * 32},
    ).scalar_one()
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text(insert.format(kind=", kind", hours=169, value=", 'workspace'")),
            {"u": user, "digest": b"w" * 32},
        )
    command.downgrade(config, "0030")
    revoked = {
        row[0]: tuple(row[1:])
        for row in connection.execute(
            text(
                "select id, revoked_at is not null, expires_at - created_at "
                "from workspace_tokens where user_id = :u"
            ),
            {"u": user},
        )
    }
    # An MCP token must not come back as a workspace token that reaches /api.
    assert revoked == {earlier: (False, timedelta(hours=168)), mcp: (True, timedelta(hours=168))}
    command.upgrade(config, "head")
    assert_models(connection)


def test_notebook_grants_take_their_approvals_operation_and_downgrade_revokes_them(
    isolated_migrations: tuple[Config, Connection],
) -> None:
    config, connection = isolated_migrations
    command.downgrade(config, "0031")
    user = connection.execute(
        text("insert into users (username, display_name) values ('grants', 'G') returning id")
    ).scalar_one()
    conversation = connection.execute(
        text("insert into conversations (owner_id, title) values (:u, 'Generated') returning id"),
        {"u": user},
    ).scalar_one()
    run = connection.execute(
        text(
            "insert into agent_runs (conversation_id, started_by, status, model, "
            "instructions_version) values (:c, :u, 'finished', 'scripted', 'generated') "
            "returning id"
        ),
        {"c": conversation, "u": user},
    ).scalar_one()
    ids = {"u": user, "c": conversation, "r": run}

    def approval(operation: str) -> object:
        return connection.execute(
            text(
                "insert into approval_requests (run_id, conversation_id, requested_by, "
                "operation, arguments, arguments_sha256, evidence, reasoning, retrieval_path, "
                "interrupt_id) values (:r, :c, :u, :o, '{}', :d, '[]', 'Generated.', '[]', :o) "
                "returning id"
            ),
            ids | {"o": operation, "d": operation.encode()},
        ).scalar_one()

    grant = (
        "insert into notebook_grants (approval_id, user_id, conversation_id, path{column}) "
        "values (:a, :u, :c, 'work/fit.ipynb'{value})"
    )
    edit = approval("workspace.edit_notebook")
    connection.execute(text(grant.format(column="", value="")), ids | {"a": edit})
    command.upgrade(config, "0032")
    grants = "select operation, revoked_at is not null from notebook_grants where user_id = :u"
    assert connection.execute(text(grants), ids).all() == [("workspace.edit_notebook", False)]
    # An approved run of the edit-granted notebook gets its own active grant.
    connection.execute(
        text(grant.format(column=", operation", value=", 'workspace.run_code'")),
        ids | {"a": approval("workspace.run_code")},
    )
    command.downgrade(config, "0031")
    # Without the operation an edit grant would cover runs again.
    revoked = "select revoked_at is not null from notebook_grants where user_id = :u"
    assert connection.execute(text(revoked), ids).scalars().all() == [True, True]
    command.upgrade(config, "head")
    assert_models(connection)
