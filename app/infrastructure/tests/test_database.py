from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database.unit_of_work import unit_of_work


@pytest.fixture
def probe_table(owner_engine: Engine) -> Iterator[str]:
    with owner_engine.begin() as connection:
        connection.execute(text("create table s0_probe (id int primary key)"))
        connection.execute(text("grant insert, delete on s0_probe to craft_app"))
    yield "s0_probe"
    with owner_engine.begin() as connection:
        connection.execute(text("drop table s0_probe"))


def test_unit_of_work_commits_and_rolls_back(app: FastAPI, probe_table: str) -> None:
    sessions = app.state.sessions
    with unit_of_work(sessions) as session:
        session.execute(text(f"insert into {probe_table} values (1)"))
    with pytest.raises(RuntimeError), unit_of_work(sessions) as session:
        session.execute(text(f"insert into {probe_table} values (2)"))
        raise RuntimeError("roll back")
    with (
        pytest.raises(DBAPIError, match="read-only transaction"),
        unit_of_work(sessions, read_only=True) as session,
    ):
        session.execute(text(f"insert into {probe_table} values (3)"))
    with unit_of_work(sessions, read_only=True) as session:
        rows = session.execute(text(f"select id from {probe_table} order by id")).scalars().all()
    assert rows == [1]


def test_test_database_is_separate(app: FastAPI) -> None:
    assert app.state.settings.database_name == "craft_test"
    with app.state.engine.connect() as connection:
        assert connection.execute(text("select current_database()")).scalar() == "craft_test"


def test_app_login_cannot_create_tables(app: FastAPI) -> None:
    with pytest.raises(DBAPIError, match="permission denied"), app.state.engine.begin() as conn:
        conn.execute(text("create table not_allowed (id int)"))
