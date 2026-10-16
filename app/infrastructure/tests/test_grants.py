"""Application privileges and relational constraints on synthetic records."""

import secrets

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

ACCOUNT_GRANTS = {
    "users": {"SELECT", "INSERT", "UPDATE"},
    "local_credentials": {"SELECT", "INSERT", "UPDATE"},
    "user_roles": {"SELECT", "INSERT", "DELETE"},
    "auth_sessions": {"SELECT", "INSERT", "UPDATE"},
    "call_log": {"SELECT", "INSERT"},
}


def assert_grants(engine: Engine, expected: dict[str, set[str]]) -> None:
    with engine.connect() as connection:
        for table, rights in expected.items():
            actual = {
                right
                for right in [
                    "SELECT",
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "TRUNCATE",
                    "REFERENCES",
                    "TRIGGER",
                ]
                if connection.execute(
                    text("select has_table_privilege('craft_app', :table, :right)"),
                    {"table": table, "right": right},
                ).scalar()
            }
            assert actual == rights, table
        assert not connection.execute(
            text("select has_schema_privilege('craft_app', 'public', 'CREATE')")
        ).scalar()


def test_accounts_call_log_grants(app: FastAPI, owner_engine: Engine) -> None:
    assert_grants(owner_engine, ACCOUNT_GRANTS)
    for verb in ("update call_log set name = name", "delete from call_log"):
        with pytest.raises(DBAPIError), app.state.engine.begin() as connection:
            connection.execute(text(verb))
    with owner_engine.begin() as connection:
        connection.execute(text("create table b01_undeclared (id int)"))
    try:
        with (
            pytest.raises(DBAPIError, match="permission denied"),
            app.state.engine.begin() as app_connection,
        ):
            app_connection.execute(text("insert into b01_undeclared values (1)"))
    finally:
        with owner_engine.begin() as connection:
            connection.execute(text("drop table b01_undeclared"))


def test_accounts_constraints(owner_engine: Engine) -> None:
    with owner_engine.connect() as connection, connection.begin() as outer:
        name = "test_" + secrets.token_hex(8)
        user_id = connection.execute(
            text("insert into users (username, display_name) values (:name, 'Test') returning id"),
            {"name": name},
        ).scalar_one()
        for invalid in (name, "MixedCase"):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("insert into users (username, display_name) values (:name, 'Test')"),
                    {"name": invalid},
                )
        for role in ("member", "admin"):
            connection.execute(
                text("insert into user_roles (user_id, role) values (:id, :role)"),
                {"id": user_id, "role": role},
            )
        for invalid in ("member", "unknown"):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("insert into user_roles (user_id, role) values (:id, :role)"),
                    {"id": user_id, "role": invalid},
                )
        sql = text("""insert into auth_sessions (user_id, token_sha256, idle_expires_at, expires_at)
                      values (:id, :digest, now() + interval '1 hour', now() + interval '1 day')""")
        args = {"id": user_id, "digest": secrets.token_bytes(32)}
        connection.execute(sql, args)
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(sql, args)
        outer.rollback()
