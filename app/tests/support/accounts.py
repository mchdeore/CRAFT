"""Signing in for tests, and the session tests' member: a conversation, probe operations and a
client that sign_in() signs in."""

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import create_app
from app.accounts import service, storage
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.operations import operation
from app.infrastructure.operations.context import response_actions
from app.infrastructure.operations.registry import registry
from app.infrastructure.shapes import Shape

ORIGIN = "http://localhost:8000"
SESSION_COOKIE = "__Host-craft_session"


def sign_in(client: TestClient, user: UUID) -> str:
    """Sign `user` in on `client` as the provider's callback does: a new browser session from
    `accounts.service.open_session`, and its cookie on the client. Returns the cookie."""
    app: Any = client.app
    caller = Caller(sessions=app.state.sessions, settings=app.state.settings)
    actions: list[dict[str, Any]] = []
    collecting = response_actions.set(actions)
    try:
        with unit_of_work(app.state.sessions) as session:
            service.open_session(caller, session, user)
    finally:
        response_actions.reset(collecting)
    (cookie,) = [action["value"] for action in actions if action["key"] == SESSION_COOKIE]
    client.cookies.set(SESSION_COOKIE, cookie)
    return cookie


class Identity(Shape):
    id: UUID
    roles: list[str]


@pytest.fixture
def account(settings, owner_engine):
    snapshot = registry.copy()
    username = "session_" + uuid4().hex
    with owner_engine.begin() as connection:
        user = connection.execute(
            storage.users.insert()
            .values(username=username, display_name="Test")
            .returning(storage.users.c.id)
        ).scalar_one()
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
        for table in [storage.auth_sessions, storage.user_roles]:
            connection.execute(table.delete().where(table.c.user_id == user))
        connection.execute(storage.users.delete().where(storage.users.c.id == user))
