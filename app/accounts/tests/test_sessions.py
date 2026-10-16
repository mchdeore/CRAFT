"""Real cookie sessions exercise expiry, revocation, Origin and trusted role scope."""

import base64
import hashlib
import io
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import create_app
from app.accounts import service, storage
from app.accounts.handlers import ResetPassword, UserRef, disable, reset
from app.infrastructure import access
from app.infrastructure.access import PUBLIC, SIGNED_IN, Caller
from app.infrastructure.errors import Forbidden
from app.infrastructure.logging import configure_logging
from app.infrastructure.operations import operation
from app.infrastructure.operations.registry import registry
from app.infrastructure.operations.storage import call_log
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


def session_row(owner_engine, user):
    with owner_engine.connect() as connection:
        return (
            connection.execute(
                select(storage.auth_sessions)
                .where(storage.auth_sessions.c.user_id == user)
                .order_by(storage.auth_sessions.c.created_at.desc())
            )
            .mappings()
            .first()
        )


def test_sign_in_sets_host_cookie(account, owner_engine) -> None:
    response = login(account)
    assert response.status_code == 200
    token = response.cookies.get("__Host-craft_session")
    raw = base64.urlsafe_b64decode(token + "=" * ((-len(token)) % 4))
    assert len(raw) == 32
    attrs = {
        part.strip().partition("=")[0].lower(): part.strip().partition("=")[2]
        for part in response.headers["set-cookie"].split(";")[1:]
    }
    assert {"secure", "httponly", "samesite", "path"} <= set(attrs)
    assert attrs["samesite"].lower() == "lax" and attrs["path"] == "/" and "domain" not in attrs
    row = session_row(owner_engine, account.id)
    assert row["token_sha256"] == hashlib.sha256(raw).digest()
    assert account.client.get("/api/account-probe").status_code == 200


def test_wrong_user_and_wrong_password_look_the_same(account, owner_engine, monkeypatch) -> None:
    verified = []
    original = PasswordHasher.verify

    def verify(self, hash, password):
        verified.append(hash == service.dummy_hash())
        return original(self, hash, password)

    monkeypatch.setattr(PasswordHasher, "verify", verify)
    a = login(account, username="unknown-" + uuid4().hex)
    b = login(account, password="wrong")
    assert (
        (a.status_code, a.json()["code"], a.json()["message"])
        == (b.status_code, b.json()["code"], b.json()["message"])
        == (401, "not_signed_in", "Not signed in.")
    )
    assert any(verified)
    with owner_engine.begin() as connection:
        connection.execute(
            storage.users.update()
            .where(storage.users.c.id == account.id)
            .values(disabled_at=access.now())
        )
    assert login(account).status_code == 401


def test_session_expiry_and_throttled_keepalive(account, owner_engine, monkeypatch) -> None:
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr(access, "now", lambda: clock[0])
    assert login(account).status_code == 200
    row = session_row(owner_engine, account.id)
    assert row["idle_expires_at"] == clock[0] + timedelta(hours=8)
    assert row["expires_at"] == clock[0] + timedelta(hours=12)
    initial = clock[0]
    clock[0] = initial + timedelta(seconds=30)
    assert account.client.get("/api/account-probe").status_code == 200
    assert session_row(owner_engine, account.id)["last_seen_at"] == initial
    clock[0] = initial + timedelta(minutes=1)
    account.client.get("/api/account-probe")
    assert session_row(owner_engine, account.id)["last_seen_at"] == clock[0]
    clock[0] = initial + timedelta(hours=8)
    assert account.client.get("/api/account-probe").status_code == 200
    assert session_row(owner_engine, account.id)["idle_expires_at"] == initial + timedelta(hours=12)
    clock[0] = initial + timedelta(hours=12)
    assert account.client.get("/api/account-probe").status_code == 401


def test_sign_out_and_password_reset_revoke_sessions(account, owner_engine) -> None:
    assert login(account).status_code == 200
    assert account.client.delete("/api/session").status_code == 204
    assert session_row(owner_engine, account.id)["revoked_reason"] == "sign_out"
    assert account.client.get("/api/account-probe").status_code == 401
    login(account)
    reset(account.admin, ResetPassword(username=account.username, password=PASSWORD + "-new"))
    assert account.client.get("/api/account-probe").status_code == 401
    service.ATTEMPTS.clear()
    login(account, password=PASSWORD + "-new")
    disable(account.admin, UserRef(username=account.username))
    assert account.client.get("/api/account-probe").status_code == 401
    with owner_engine.connect() as connection:
        assert (
            connection.execute(
                text("select count(*) from conversations where id=:id"),
                {"id": account.conversation},
            ).scalar()
            == 1
        )


def test_cross_origin_write_is_refused(account) -> None:
    called = []
    snapshot = registry.copy()
    for verb in ["post", "put", "patch", "delete"]:

        def write(caller: Caller) -> None:
            called.append(True)

        write.__name__ = "write_" + verb
        path = "/origin-" + verb
        decorator = (
            operation(PUBLIC, post=path)
            if verb == "post"
            else operation(PUBLIC, put=path)
            if verb == "put"
            else operation(PUBLIC, patch=path)
            if verb == "patch"
            else operation(PUBLIC, delete=path)
        )
        decorator(write)
    app = create_app(account.app.state.settings)
    try:
        with TestClient(app, base_url="https://testserver") as client:
            for verb in ["POST", "PUT", "PATCH", "DELETE"]:
                for headers in [{}, {"Origin": "https://foreign.invalid"}, {"Origin": "null"}]:
                    assert (
                        client.request(
                            verb, "/api/origin-" + verb.lower(), headers=headers
                        ).status_code
                        == 403
                    )
                assert not called
            for verb in ["POST", "PUT", "PATCH", "DELETE"]:
                assert (
                    client.request(
                        verb, "/api/origin-" + verb.lower(), headers={"Origin": ORIGIN}
                    ).status_code
                    == 204
                )
        assert len(called) == 4
    finally:
        registry.clear()
        registry.update(snapshot)


def test_role_change_applies_next_request(account, owner_engine) -> None:
    login(account)
    assert account.client.get("/api/account-admin").status_code == 403
    with owner_engine.begin() as connection:
        connection.execute(storage.user_roles.insert().values(user_id=account.id, role="admin"))
    assert account.client.get("/api/account-admin").status_code == 204
    assert "admin" in account.client.get("/api/account-probe").json()["roles"]
    with owner_engine.begin() as connection:
        connection.execute(
            storage.user_roles.delete().where(
                storage.user_roles.c.user_id == account.id, storage.user_roles.c.role == "admin"
            )
        )
    assert account.client.get("/api/account-admin").status_code == 403


def test_sign_in_rate_limited_and_tokens_never_logged(account, owner_engine) -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    a = login(account)
    b = login(account)
    raw_a = a.cookies.get("__Host-craft_session")
    raw_b = b.cookies.get("__Host-craft_session")
    assert hashlib.sha256(raw_a.encode()).digest() != hashlib.sha256(raw_b.encode()).digest()
    for _ in range(3):
        login(account, password="wrong")
    assert login(account).status_code == 429
    leak = PASSWORD in stream.getvalue() or raw_a in stream.getvalue() or raw_b in stream.getvalue()
    assert not leak
    with owner_engine.connect() as connection:
        cells = (
            connection.execute(select(call_log).where(call_log.c.user_id == account.id))
            .mappings()
            .all()
        )
    leak = any(
        PASSWORD in str(cell) or raw_a in str(cell) or raw_b in str(cell)
        for row in cells
        for cell in row.values()
    )
    assert not leak


def test_http_python_access_and_logging_agree(account, owner_engine) -> None:
    login(account)
    caller = Caller(
        user_id=account.id,
        roles=frozenset({"member"}),
        sessions=account.app.state.sessions,
        settings=account.app.state.settings,
    )
    with pytest.raises(Forbidden):
        account.probe(caller)
    response = account.client.get("/api/account-admin")
    assert response.status_code == 403
    with owner_engine.connect() as connection:
        rows = (
            connection.execute(
                select(call_log)
                .where(call_log.c.name.endswith(".admin_probe"), call_log.c.user_id == account.id)
                .order_by(call_log.c.id.desc())
                .limit(2)
            )
            .mappings()
            .all()
        )
    assert {r["origin"] for r in rows} == {"http", "python"} and all(
        r["error_code"] == "forbidden" for r in rows
    )
    http = next(r for r in rows if r["origin"] == "http")
    assert (
        http["auth_session_id"] is not None
        and str(http["request_id"]) == response.headers["x-request-id"]
    )


def test_reset_during_password_verification_refuses_old_password(
    account, owner_engine, monkeypatch
) -> None:
    original = PasswordHasher.verify
    reset_once = []

    def verify(self, hash, password):
        verified = original(self, hash, password)
        if verified and not reset_once:
            reset_once.append(True)
            reset(
                account.admin,
                ResetPassword(username=account.username, password=PASSWORD + "-replacement"),
            )
        return verified

    monkeypatch.setattr(PasswordHasher, "verify", verify)
    assert login(account).status_code == 401
    with owner_engine.connect() as connection:
        count = connection.execute(
            select(storage.auth_sessions.c.id).where(storage.auth_sessions.c.user_id == account.id)
        ).all()
    assert len(count) == 0


def test_reset_password_can_sign_in_without_an_unadopted_upper_limit(account) -> None:
    password = "s" * 1001
    reset(account.admin, ResetPassword(username=account.username, password=password))
    assert login(account, password=password).status_code == 200


def test_long_username_can_sign_in(account, owner_engine) -> None:
    username = "synthetic_" + "u" * 101
    with owner_engine.begin() as connection:
        connection.execute(
            storage.users.update().where(storage.users.c.id == account.id).values(username=username)
        )
    account.username = username
    assert login(account).status_code == 200
