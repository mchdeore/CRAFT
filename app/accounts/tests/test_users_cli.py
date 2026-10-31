import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.accounts import storage
from app.cli import main
from app.infrastructure.errors import Conflict
from app.tests.support import people
from app.tests.support.accounts import sign_in


@pytest.fixture
def actor(owner_engine, settings, monkeypatch):
    """An admin's username for `craft users --as`, with the CLI on the test database."""
    name = "cli_admin_" + uuid4().hex
    with owner_engine.begin() as connection:
        admin = connection.execute(
            storage.users.insert()
            .values(username=name, display_name="Test admin")
            .returning(storage.users.c.id)
        ).scalar_one()
        connection.execute(storage.user_roles.insert().values(user_id=admin, role="admin"))
    monkeypatch.setenv("CRAFT_DATABASE_NAME", settings.database_name)
    yield name
    with owner_engine.begin() as connection:
        connection.execute(storage.user_roles.delete().where(storage.user_roles.c.user_id == admin))
        connection.execute(storage.users.delete().where(storage.users.c.id == admin))


def test_users_add_takes_the_email_people_sign_in_with(owner_engine, actor, capsys) -> None:
    name = "cli_" + uuid4().hex
    with pytest.raises(SystemExit):  # no account without an email to sign in with
        main(["users", "--as", actor, "add", name])
    main(["users", "--as", actor, "add", name, "--email", f"{name}@example.org"])
    capsys.readouterr()
    with pytest.raises(Conflict):
        main(["users", "--as", actor, "add", name.upper(), "--email", "other@example.org"])
    main(["users", "--as", actor, "list"])
    output = json.loads(capsys.readouterr().out)
    (row,) = [row for row in output if row["username"] == name]
    assert row["email"] == f"{name}@example.org"
    assert all(
        set(row) == {"id", "username", "displayName", "roles", "language", "email"}
        for row in output
    )
    with owner_engine.begin() as connection:
        user = connection.execute(
            select(storage.users.c.id).where(storage.users.c.username == name)
        ).scalar_one()
        connection.execute(storage.user_roles.delete().where(storage.user_roles.c.user_id == user))
        connection.execute(storage.users.delete().where(storage.users.c.id == user))


def test_users_sign_out_ends_that_accounts_sessions(app, owner_engine, actor) -> None:
    sessions = storage.auth_sessions
    with (
        people.person(owner_engine) as someone,
        people.person(owner_engine) as bystander,
        TestClient(app, base_url="https://testserver") as browser,
        TestClient(app, base_url="https://testserver") as other,
    ):
        sign_in(browser, someone.id)
        sign_in(other, bystander.id)
        main(["users", "--as", actor, "sign-out", someone.username])
        assert browser.get("/api/me").status_code == 401
        assert other.get("/api/me").status_code == 200
        with owner_engine.connect() as connection:
            reasons = connection.execute(
                select(sessions.c.revoked_reason).where(sessions.c.user_id == someone.id)
            ).scalars()
            assert list(reasons) == ["admin_sign_out"]
