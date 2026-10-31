import pytest

from app.infrastructure.database import metadata as m
from app.tests.support.chat import chat as chat
from app.tests.support.cube import cube_source as cube_source


@pytest.fixture
def foreign_http(chat):
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from app import create_app
    from app.tests.support.accounts import sign_in

    username = "foreign_" + uuid4().hex
    users = m.metadata.tables["users"]
    with chat["owner"].begin() as connection:
        user = connection.execute(
            users.insert()
            .values(username=username, display_name="Synthetic foreign account")
            .returning(users.c.id)
        ).scalar_one()
        connection.execute(
            m.metadata.tables["user_roles"].insert().values(user_id=user, role="member")
        )
    try:
        with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
            sign_in(http, user)
            yield http
    finally:
        with chat["owner"].begin() as connection:
            for name in ["auth_sessions", "user_roles"]:
                table = m.metadata.tables[name]
                connection.execute(table.delete().where(table.c.user_id == user))
            connection.execute(users.delete().where(users.c.id == user))
