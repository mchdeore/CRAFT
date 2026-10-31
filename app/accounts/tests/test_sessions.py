"""Real cookie sessions exercise expiry, revocation, Origin and trusted role scope. Tests sign in
as the provider's callback does (`sign_in`); test_oidc.py covers the callback itself."""

import base64
import hashlib
import io
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import create_app
from app.accounts import service, storage
from app.accounts.handlers import UserRef, disable, end_sessions
from app.infrastructure import access
from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.errors import Forbidden
from app.infrastructure.logging import configure_logging
from app.infrastructure.operations import operation
from app.infrastructure.operations.registry import registry
from app.infrastructure.operations.storage import call_log
from app.tests.support.accounts import ORIGIN, sign_in


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


def test_a_session_cookie_is_32_bytes_whose_digest_is_stored(account, owner_engine) -> None:
    cookie = sign_in(account.client, account.id)
    raw = base64.urlsafe_b64decode(cookie + "=" * ((-len(cookie)) % 4))
    assert len(raw) == 32
    assert session_row(owner_engine, account.id)["token_sha256"] == hashlib.sha256(raw).digest()
    assert account.client.get("/api/account-probe").status_code == 200


def test_session_expiry_and_throttled_keepalive(account, owner_engine, monkeypatch) -> None:
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr(access, "now", lambda: clock[0])
    sign_in(account.client, account.id)
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


def test_signing_out_an_admin_sign_out_and_disabling_end_sessions(account, owner_engine) -> None:
    sign_in(account.client, account.id)
    assert account.client.delete("/api/session").status_code == 204
    assert session_row(owner_engine, account.id)["revoked_reason"] == "sign_out"
    assert account.client.get("/api/account-probe").status_code == 401
    sign_in(account.client, account.id)
    end_sessions(account.admin, UserRef(username=account.username))
    assert session_row(owner_engine, account.id)["revoked_reason"] == "admin_sign_out"
    assert account.client.get("/api/account-probe").status_code == 401
    sign_in(account.client, account.id)
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


def test_resolve_account_finds_only_an_enabled_account(app, settings, owner_engine) -> None:
    username = "local_" + uuid4().hex
    with owner_engine.begin() as connection:
        user = connection.execute(
            storage.users.insert()
            .values(username=username, display_name="Test")
            .returning(storage.users.c.id)
        ).scalar_one()
        connection.execute(storage.user_roles.insert().values(user_id=user, role="member"))
    anonymous = Caller(sessions=app.state.sessions, settings=settings, origin="mcp")
    try:
        found = service.resolve_account(anonymous, username.upper())
        assert (found.user_id, found.roles, found.origin) == (user, frozenset({"member"}), "mcp")
        assert service.resolve_account(anonymous, "unknown_" + uuid4().hex) == anonymous
        with owner_engine.begin() as connection:
            connection.execute(
                storage.users.update()
                .where(storage.users.c.id == user)
                .values(disabled_at=access.now())
            )
        assert service.resolve_account(anonymous, username) == anonymous
    finally:
        with owner_engine.begin() as connection:
            connection.execute(
                storage.user_roles.delete().where(storage.user_roles.c.user_id == user)
            )
            connection.execute(storage.users.delete().where(storage.users.c.id == user))


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
    sign_in(account.client, account.id)
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


def test_session_cookies_differ_and_are_never_logged(account, owner_engine) -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    first = sign_in(account.client, account.id)
    assert account.client.get("/api/account-probe").status_code == 200
    second = sign_in(account.client, account.id)
    assert account.client.get("/api/account-probe").status_code == 200
    assert first != second
    assert first not in stream.getvalue() and second not in stream.getvalue()
    with owner_engine.connect() as connection:
        rows = (
            connection.execute(select(call_log).where(call_log.c.user_id == account.id))
            .mappings()
            .all()
        )
    assert rows and not any(
        first in str(cell) or second in str(cell) for row in rows for cell in row.values()
    )


def test_http_python_access_and_logging_agree(account, owner_engine) -> None:
    sign_in(account.client, account.id)
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


PAGES_ORIGIN = "https://pages.craft.test"


@pytest.fixture
def cross_origin_client(account, settings):
    import msgspec

    configured = msgspec.structs.replace(
        settings,
        cross_origin_browser=True,
        allowed_origins=PAGES_ORIGIN + ",https://api.craft.test",
    )
    with TestClient(
        create_app(configured),
        base_url="https://api.craft.test",
        headers={"Origin": PAGES_ORIGIN},
    ) as client:
        yield client


def test_default_browser_mode_does_not_allow_cross_origin_preflight(account):
    response = account.client.options(
        "/api/session",
        headers={
            "Origin": PAGES_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 404
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("headers", ["content-type", "content-type,x-file-name,x-simulated"])
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("origin", [PAGES_ORIGIN, "https://api.craft.test"])
def test_enabled_browser_preflight_allows_only_configured_origin(
    cross_origin_client, headers, method, origin
):
    response = cross_origin_client.options(
        "/api/session",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )
    assert response.status_code == 200
    assert UUID(response.headers["x-request-id"])
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in response.headers["vary"]
    assert "*" not in response.headers["access-control-allow-headers"]
    assert "TRACE" not in response.headers["access-control-allow-methods"]


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://untrusted.craft.test", "Access-Control-Request-Method": "POST"},
        {"Origin": "null", "Access-Control-Request-Method": "POST"},
        {"Access-Control-Request-Method": "TRACE"},
        {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-untrusted"},
    ],
)
def test_enabled_browser_preflight_refuses_unconfigured_request(cross_origin_client, headers):
    response = cross_origin_client.options("/api/session", headers=headers)
    assert response.status_code == 400
    assert UUID(response.headers["x-request-id"])
    if "Origin" in headers:
        assert "access-control-allow-origin" not in response.headers


def test_enabled_browser_sign_out_keeps_cookie_security(account, cross_origin_client):
    cookie = sign_in(cross_origin_client, account.id)
    identity = cross_origin_client.get("/api/account-probe")
    assert identity.status_code == 200 and identity.json()["id"] == str(account.id)
    assert identity.headers["access-control-allow-origin"] == PAGES_ORIGIN
    signed_out = cross_origin_client.delete("/api/session")
    assert signed_out.status_code == 204
    set_cookie = signed_out.headers["set-cookie"].lower()
    assert "samesite=none" in set_cookie and "secure" in set_cookie and "httponly" in set_cookie
    assert "path=/" in set_cookie and "domain=" not in set_cookie and "max-age=0" in set_cookie
    assert "set-cookie" not in signed_out.headers["access-control-expose-headers"].lower()
    cross_origin_client.cookies.clear()
    cross_origin_client.headers["Cookie"] = "__Host-craft_session=" + cookie
    revoked = cross_origin_client.get("/api/me")
    assert revoked.status_code == 401
    assert revoked.headers["access-control-allow-origin"] == PAGES_ORIGIN


def test_enabled_browser_origin_guard_refuses_a_foreign_sign_out(
    account, cross_origin_client, owner_engine
):
    sign_in(cross_origin_client, account.id)
    for origin in ("https://untrusted.craft.test", "null", None):
        if origin is None:
            cross_origin_client.headers.pop("Origin", None)
        else:
            cross_origin_client.headers["Origin"] = origin
        response = cross_origin_client.delete("/api/session")
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers
        assert "set-cookie" not in response.headers
    assert session_row(owner_engine, account.id)["revoked_at"] is None
    cross_origin_client.headers["Origin"] = PAGES_ORIGIN
    assert cross_origin_client.get("/api/me").status_code == 200


def test_enabled_browser_fresh_roles_and_expiry(
    account, cross_origin_client, owner_engine, monkeypatch
):
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr(access, "now", lambda: clock[0])
    sign_in(cross_origin_client, account.id)
    denied = cross_origin_client.get("/api/account-admin")
    assert (
        denied.status_code == 403 and denied.headers["access-control-allow-origin"] == PAGES_ORIGIN
    )
    with owner_engine.begin() as connection:
        connection.execute(storage.user_roles.insert().values(user_id=account.id, role="admin"))
    assert cross_origin_client.get("/api/account-admin").status_code == 204
    with owner_engine.begin() as connection:
        connection.execute(
            storage.user_roles.delete().where(
                storage.user_roles.c.user_id == account.id, storage.user_roles.c.role == "admin"
            )
        )
    assert cross_origin_client.get("/api/account-admin").status_code == 403
    clock[0] += timedelta(hours=12)
    expired = cross_origin_client.get("/api/me")
    assert (
        expired.status_code == 401
        and expired.headers["access-control-allow-origin"] == PAGES_ORIGIN
    )


def test_enabled_browser_handled_errors_keep_exact_cors(account, cross_origin_client):
    anonymous = cross_origin_client.get("/api/me")
    sign_in(cross_origin_client, account.id)
    not_found = cross_origin_client.get("/api/no-such-fixture-route")
    invalid = cross_origin_client.patch("/api/me", json={"language": "de"})
    forbidden = cross_origin_client.get("/api/account-admin")
    responses = (anonymous, not_found, invalid, forbidden)
    assert [response.status_code for response in responses] == [401, 404, 400, 403]
    for response in responses:
        assert response.headers["access-control-allow-origin"] == PAGES_ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"


def test_enabled_browser_can_update_its_owned_chat(account, cross_origin_client):
    sign_in(cross_origin_client, account.id)
    response = cross_origin_client.patch(
        "/api/conversations/" + str(account.conversation),
        json={"title": "Generated renamed conversation"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == PAGES_ORIGIN
    assert response.json()["id"] == str(account.conversation)
    assert response.json()["title"] == "Generated renamed conversation"
    reopened = cross_origin_client.get("/api/conversations/" + str(account.conversation))
    assert (
        reopened.status_code == 200 and reopened.json()["title"] == "Generated renamed conversation"
    )
