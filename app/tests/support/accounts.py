"""A signed-in session account with probe operations, as the session tests use."""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import create_app
from app.accounts import service, storage
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.operations.registry import registry
from app.infrastructure.shapes import Shape

PASSWORD = "synthetic-session-password"
ORIGIN = "http://localhost:8000"


class Identity(Shape):
    id: UUID
    roles: list[str]


@pytest.fixture
def account(settings, owner_engine):
    snapshot = registry.copy()
    service.ATTEMPTS.clear()
    username = "session_" + uuid4().hex
    hashed = service.PASSWORDS.hash(PASSWORD)
    with owner_engine.begin() as connection:
        user = connection.execute(
            storage.users.insert()
            .values(username=username, display_name="Test")
            .returning(storage.users.c.id)
        ).scalar_one()
        connection.execute(
            storage.local_credentials.insert().values(user_id=user, password_hash=hashed)
        )
        connection.execute(storage.user_roles.insert().values(user_id=user, role="member"))
        conversation = connection.execute(
            text("insert into conversations (owner_id,title) values (:id,'Test') returning id"),
            {"id": user},
        ).scalar_one()

    @operation(SIGNED_IN, get="/account-probe")
    def account_probe(caller: Caller) -> Identity:
        assert caller.user_id is not None
        return Identity(id=caller.user_id, roles=sorted(caller.roles))

    @operation("accounts.manage", get="/account-admin")
    def admin_probe(caller: Caller) -> None:
        return None

    app = create_app(settings)
    with TestClient(app, base_url="https://testserver", headers={"Origin": ORIGIN}) as client:
        yield SimpleNamespace(
            id=user,
            username=username,
            conversation=conversation,
            app=app,
            client=client,
            admin=Caller(
                user_id=uuid4(),
                roles=frozenset({"admin"}),
                sessions=app.state.sessions,
                settings=settings,
            ),
            probe=admin_probe,
        )
    registry.clear()
    registry.update(snapshot)
    with owner_engine.begin() as connection:
        connection.execute(text("delete from conversations where id=:id"), {"id": conversation})
        for table in [storage.auth_sessions, storage.local_credentials, storage.user_roles]:
            connection.execute(table.delete().where(table.c.user_id == user))
        connection.execute(storage.users.delete().where(storage.users.c.id == user))


def login(account, password=PASSWORD, username=None):
    return account.client.post(
        "/api/session", json={"username": username or account.username, "password": password}
    )
