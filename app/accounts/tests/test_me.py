import json
from datetime import timedelta

import jsonschema
from fastapi.testclient import TestClient

from app.accounts import storage
from app.accounts.tests.test_sessions import account as account
from app.accounts.tests.test_sessions import login
from app.infrastructure import access
from app.tests.test_contract import typescript_schema


def test_me_returns_signed_in_account(account, owner_engine) -> None:
    assert account.client.get("/api/me").status_code == 401
    assert login(account).status_code == 200
    response = account.client.get("/api/me")
    assert response.status_code == 200
    assert response.json() == {
        "id": str(account.id),
        "username": account.username,
        "displayName": "Test",
        "roles": ["member"],
    }
    jsonschema.validate(response.json(), typescript_schema("Me"))
    with owner_engine.begin() as connection:
        connection.execute(storage.user_roles.insert().values(user_id=account.id, role="admin"))
    assert account.client.get("/api/me").json()["roles"] == ["admin", "member"]
    with owner_engine.begin() as connection:
        connection.execute(
            storage.auth_sessions.update()
            .where(storage.auth_sessions.c.user_id == account.id)
            .values(idle_expires_at=access.now() - timedelta(seconds=1))
        )
    assert account.client.get("/api/me").status_code == 401
    assert login(account).status_code == 200
    assert account.client.delete("/api/session").status_code == 204
    assert account.client.get("/api/me").status_code == 401
    with TestClient(account.app) as anonymous:
        assert anonymous.get("/api/me").status_code == 401


def test_me_ignores_browser_identity(account) -> None:
    login(account)
    assert account.client.get("/api/me?userId=another").status_code == 400
    assert (
        account.client.request(
            "GET", "/api/me", content=json.dumps({"userId": "another"})
        ).status_code
        == 400
    )
    response = account.client.get("/api/me", headers={"X-User-Id": "another", "X-Roles": "admin"})
    assert response.json()["id"] == str(account.id)
    assert set(response.json()) == {"id", "username", "displayName", "roles"}
    jsonschema.validate(json.load(open("api/examples/me.json")), typescript_schema("Me"))
