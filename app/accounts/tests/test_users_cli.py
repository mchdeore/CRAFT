import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.accounts import storage
from app.cli import main
from app.infrastructure.errors import Conflict, InvalidInput


def test_users_add_list_and_password_policy(owner_engine, monkeypatch, capsys) -> None:
    name = "cli_" + uuid4().hex
    actor = "cli_admin_" + uuid4().hex
    with owner_engine.begin() as connection:
        admin = connection.execute(
            storage.users.insert()
            .values(username=actor, display_name="Test admin")
            .returning(storage.users.c.id)
        ).scalar_one()
        connection.execute(storage.user_roles.insert().values(user_id=admin, role="admin"))
    monkeypatch.setenv("CRAFT_DATABASE_NAME", "craft_test")
    monkeypatch.setattr("getpass.getpass", lambda _: "short")
    with pytest.raises(InvalidInput):
        main(["users", "--as", actor, "add", name])
    monkeypatch.setattr("getpass.getpass", lambda _: "synthetic-cli-password")
    main(["users", "--as", actor, "add", name])
    capsys.readouterr()
    with pytest.raises(Conflict):
        main(["users", "--as", actor, "add", name.upper()])
    main(["users", "--as", actor, "list"])
    output = json.loads(capsys.readouterr().out)
    assert any(row["username"] == name for row in output)
    assert all(set(row) == {"id", "username", "displayName", "roles", "language"} for row in output)
    with owner_engine.begin() as connection:
        user = connection.execute(
            select(storage.users.c.id).where(storage.users.c.username == name)
        ).scalar_one()
        connection.execute(
            storage.local_credentials.delete().where(storage.local_credentials.c.user_id == user)
        )
        connection.execute(storage.user_roles.delete().where(storage.user_roles.c.user_id == user))
        connection.execute(storage.users.delete().where(storage.users.c.id == user))
        connection.execute(storage.user_roles.delete().where(storage.user_roles.c.user_id == admin))
        connection.execute(storage.users.delete().where(storage.users.c.id == admin))
