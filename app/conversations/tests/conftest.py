import pytest
from sqlalchemy import select

from app.agent.tests.test_chat_stream import chat as chat
from app.cube.tests.conftest import cube_source as cube_source
from app.infrastructure.database import metadata as m


@pytest.fixture(autouse=True)
def remove_extra_conversations(chat):
    yield
    table = m.metadata.tables["conversations"]
    with chat["owner"].begin() as connection:
        ids = list(
            connection.execute(
                select(table.c.id).where(table.c.owner_id == chat["caller"].user_id)
            ).scalars()
        )
        for name in ["workspace_files", "messages", "agent_sessions", "agent_runs"]:
            owned = m.metadata.tables[name]
            connection.execute(owned.delete().where(owned.c.conversation_id.in_(ids)))
        connection.execute(table.delete().where(table.c.id.in_(ids)))


@pytest.fixture
def foreign_http(chat):
    from uuid import uuid4

    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    from app import create_app

    username, password = "foreign_" + uuid4().hex, uuid4().hex
    hashed = PasswordHasher().hash(password)
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
        connection.execute(
            m.metadata.tables["local_credentials"]
            .insert()
            .values(user_id=user, password_hash=hashed)
        )
    try:
        with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
            signed = http.post(
                "/api/session",
                json={"username": username, "password": password},
                headers={"Origin": "https://testserver"},
            )
            assert signed.status_code == 200
            yield http
    finally:
        with chat["owner"].begin() as connection:
            for name in ["auth_sessions", "local_credentials", "user_roles"]:
                table = m.metadata.tables[name]
                connection.execute(table.delete().where(table.c.user_id == user))
            connection.execute(users.delete().where(users.c.id == user))
