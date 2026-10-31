"""Browser sign-in through the identity provider: craft-app -> craft-id -> craft-app."""

import time
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

import httpx
import jwt
import msgspec
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import create_app
from app.accounts import oidc, storage
from app.accounts.handlers import AccessApproval, approve
from app.identity import service as identity
from app.infrastructure import access
from app.infrastructure.access import Caller
from app.infrastructure.config.settings import Settings
from app.tests.support.identity import PUBLIC, REDIRECT, Provider, keys, provider  # noqa: F401


@pytest.fixture
def site(settings, provider: Provider, owner_engine, monkeypatch, request):  # noqa: F811
    """craft-app signing people in at a stand-in craft-id; `cross-origin` (indirect) turns on
    credentialed cross-origin browsing."""
    monkeypatch.setattr(oidc, "http", provider.client)
    monkeypatch.setattr(oidc, "_metadata", {})
    monkeypatch.setattr(oidc, "_keys", {})
    configured = msgspec.structs.replace(
        settings,
        oidc_authority=provider.authority,
        oidc_client_id=provider.client_id,
        oidc_client_secret=provider.secret,
        oidc_redirect_uri=REDIRECT,
    )
    if getattr(request, "param", None) == "cross-origin":
        configured = msgspec.structs.replace(
            configured, cross_origin_browser=True, allowed_origins=PUBLIC
        )
    app = create_app(configured)
    created = []
    with TestClient(app, base_url=PUBLIC, follow_redirects=False) as browser:
        yield SimpleNamespace(
            browser=browser, provider=provider, settings=configured, app=app, created=created
        )
    with owner_engine.begin() as connection:
        for user in created:
            connection.execute(text("delete from auth_sessions where user_id=:id"), {"id": user})
            connection.execute(text("delete from user_roles where user_id=:id"), {"id": user})
            connection.execute(text("delete from users where id=:id"), {"id": user})


def craft_user(site, owner_engine, email):
    with owner_engine.begin() as connection:
        user = connection.execute(
            storage.users.insert()
            .values(username="oidc_" + uuid4().hex[:10], display_name="Test", email=email)
            .returning(storage.users.c.id)
        ).scalar_one()
        connection.execute(storage.user_roles.insert().values(user_id=user, role="member"))
    site.created.append(user)
    return user


def sign_in(site, username, password, return_to="/after"):
    """The browser's round trip; returns the final redirect from craft-app."""
    start = site.browser.get("/api/auth/login", params={"returnTo": return_to})
    assert start.status_code == 302
    authorize = start.headers["location"]
    assert authorize.startswith(site.provider.authority.removesuffix("/v2.0"))
    form = dict(parse_qsl(urlsplit(authorize).query))
    answer = site.provider.client.post(
        authorize.split("?")[0], data=form | {"username": username, "password": password}
    )
    assert answer.status_code == 302
    return site.browser.get(answer.headers["location"])


@pytest.mark.parametrize("site", ["same-origin", "cross-origin"], indirect=True)
def test_sign_in_links_by_email_and_sets_only_the_session_cookie(site, owner_engine) -> None:
    username, password, oid = site.provider.account(email=f"{uuid4().hex[:8]}@example.org")
    with site.provider.sessions() as session:
        email = identity.storage.account(session, username)["email"]
    user = craft_user(site, owner_engine, email)

    done = sign_in(site, username, password)

    assert done.status_code == 302 and done.headers["location"] == "/after"
    assert site.browser.cookies.get("__Host-craft_session")
    assert not site.browser.cookies.get("__Host-craft_signin")
    set_cookies = done.headers.get_list("set-cookie")
    (cookie,) = [c for c in set_cookies if c.startswith("__Host-craft_session=")]
    attributes = {
        part.strip().partition("=")[0].lower(): part.strip().partition("=")[2]
        for part in cookie.split(";")[1:]
    }
    assert {"secure", "httponly", "samesite", "path"} <= set(attributes)
    # A frontend on another origin needs the cookie on its cross-site requests.
    same_site = "none" if site.settings.cross_origin_browser else "lax"
    assert attributes["samesite"].lower() == same_site and attributes["path"] == "/"
    assert "domain" not in attributes
    me = site.browser.get("/api/me").json()
    assert me["id"] == str(user) and me["email"] == email
    with owner_engine.connect() as connection:
        row = connection.execute(select(storage.users).where(storage.users.c.id == user)).one()
    assert (str(row.idp_tenant_id), row.idp_object_id) == (str(identity.TENANT), oid)


def test_signing_in_again_ends_the_earlier_session(site, owner_engine) -> None:
    username, password, _ = site.provider.account(email=f"{uuid4().hex[:8]}@example.org")
    with site.provider.sessions() as session:
        email = identity.storage.account(session, username)["email"]
    craft_user(site, owner_engine, email)
    sign_in(site, username, password)
    earlier = site.browser.cookies["__Host-craft_session"]

    assert sign_in(site, username, password).headers["location"] == "/after"

    assert site.browser.get("/api/me").status_code == 200
    # The browser holds only the new cookie; a copy of the earlier one must not sign anyone in.
    site.browser.cookies.clear()
    site.browser.cookies.set("__Host-craft_session", earlier)
    assert site.browser.get("/api/me").status_code == 401


def test_a_disabled_account_gets_no_access(site, owner_engine) -> None:
    username, password, _ = site.provider.account(email=f"{uuid4().hex[:8]}@example.org")
    with site.provider.sessions() as session:
        email = identity.storage.account(session, username)["email"]
    user = craft_user(site, owner_engine, email)
    with owner_engine.begin() as connection:
        connection.execute(
            storage.users.update()
            .where(storage.users.c.id == user)
            .values(disabled_at=access.now())
        )

    refused = sign_in(site, username, password)

    assert refused.headers["location"] == "/?signIn=no-access"
    assert not site.browser.cookies.get("__Host-craft_session")
    with owner_engine.connect() as connection:
        sessions = storage.auth_sessions
        assert not connection.execute(select(sessions).where(sessions.c.user_id == user)).all()


def test_unknown_person_gets_no_access_until_approved(site, owner_engine) -> None:
    email = f"{uuid4().hex[:8]}@example.org"
    username, password, _ = site.provider.account(email=email)

    refused = sign_in(site, username, password)

    assert refused.headers["location"] == "/?signIn=no-access"
    assert not site.browser.cookies.get("__Host-craft_session")
    admin = Caller(
        user_id=uuid4(),
        roles=frozenset({"admin"}),
        sessions=site.app.state.sessions,
        settings=site.settings,
    )
    approved = approve(admin, AccessApproval(email=email))
    site.created.append(approved.id)
    assert sign_in(site, username, password).headers["location"] == "/after"
    assert site.browser.get("/api/me").json()["id"] == str(approved.id)


def test_return_to_stays_on_allowed_origins(site) -> None:
    from app.accounts.service import return_target

    assert return_target(site.settings, "/x?y=1") == "/x?y=1"
    for value in ["//evil.example/x", "https://evil.example/", "/\\evil.example", "javascript:x"]:
        assert return_target(site.settings, value) == "/"


def test_there_is_no_password_sign_in(site) -> None:
    response = site.browser.post(
        "/api/session",
        json={"username": "x", "password": "y"},
        headers={"Origin": site.settings.allowed_origins.split(",")[0]},
    )
    assert response.status_code == 404  # only DELETE /api/session, signing out, is left


def forged(site, flow, **changes):
    now = int(time.time())
    claims = {
        "iss": site.provider.authority,
        "aud": site.provider.client_id,
        "tid": str(identity.TENANT),
        "oid": str(uuid4()),
        "nonce": flow["nonce"],
        "iat": now,
        "nbf": now,
        "exp": now + 600,
    } | changes
    return site.provider.keys.sign(claims)


@pytest.mark.parametrize(
    "change",
    [
        {"iss": "https://login.microsoftonline.com/other/v2.0"},
        {"aud": str(uuid4())},
        {"nonce": "replayed"},
        {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200},
        "signature",
    ],
)
def test_bad_id_tokens_are_refused(site, monkeypatch, change) -> None:
    _, flow = oidc.begin(site.settings)
    token = forged(site, flow, **(change if isinstance(change, dict) else {}))
    if change == "signature":
        other = identity.Keys()
        other.kid = site.provider.keys.kid
        token = other.sign(jwt.decode(token, options={"verify_signature": False}))
    answer = SimpleNamespace(status_code=200, json=lambda: {"id_token": token})
    monkeypatch.setattr(site.provider.client, "post", lambda *a, **k: answer, raising=False)
    with pytest.raises(oidc.SignInFailed):
        oidc.finish(site.settings, flow, flow["state"], "code")


def test_codes_are_single_use_and_redirect_uris_exact(site) -> None:
    username, password, _ = site.provider.account()
    url, flow = oidc.begin(site.settings)
    form = dict(parse_qsl(urlsplit(url).query))
    endpoint = url.split("?")[0]
    unlisted = site.provider.client.get(endpoint, params=form | {"redirect_uri": PUBLIC + "/x"})
    assert unlisted.status_code == 400
    answer = site.provider.client.post(
        endpoint, data=form | {"username": username, "password": password}
    )
    code = dict(parse_qsl(urlsplit(answer.headers["location"]).query))["code"]
    with pytest.raises(oidc.SignInFailed):
        oidc.finish(site.settings, flow, "other-state", code)
    with pytest.raises(oidc.SignInFailed):  # the code was not spent by the refusal above
        oidc.finish(site.settings, flow | {"verifier": "wrong"}, flow["state"], code)
    with pytest.raises(oidc.SignInFailed):  # ...but a wrong verifier spends it
        oidc.finish(site.settings, flow, flow["state"], code)


def test_only_the_three_provider_settings_change_for_entra() -> None:
    provider_fields = {name for name in Settings.__struct_fields__ if name.startswith("oidc_")}
    assert provider_fields - {"oidc_redirect_uri"} == {
        "oidc_authority",
        "oidc_client_id",
        "oidc_client_secret",
    }


@pytest.mark.parametrize("document", ["list", "no-endpoints"])
def test_a_malformed_discovery_document_is_refused(site, monkeypatch, document) -> None:
    document = [] if document == "list" else {"issuer": site.provider.authority}
    answer = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: document)
    monkeypatch.setattr(site.provider.client, "get", lambda *a, **k: answer)
    with pytest.raises(oidc.ApiError):
        oidc.metadata(site.settings.oidc_authority)


def test_a_non_uuid_object_id_is_refused(site, monkeypatch) -> None:
    _, flow = oidc.begin(site.settings)
    answer = SimpleNamespace(
        status_code=200, json=lambda: {"id_token": forged(site, flow, oid="x")}
    )
    monkeypatch.setattr(site.provider.client, "post", lambda *a, **k: answer, raising=False)
    with pytest.raises(oidc.SignInFailed):
        oidc.finish(site.settings, flow, flow["state"], "code")


def unreachable(site, monkeypatch) -> None:
    """The provider stops answering, and the discovery document it served is forgotten."""

    def down(*args, **kwargs):
        raise httpx.ConnectError("the provider is down")

    monkeypatch.setattr(oidc, "_metadata", {})
    monkeypatch.setattr(site.provider.client, "get", down)


def test_an_unreachable_provider_at_the_callback_fails_the_sign_in(site, monkeypatch) -> None:
    username, password, _ = site.provider.account()
    start = site.browser.get("/api/auth/login", params={"returnTo": "/after"})
    authorize = start.headers["location"]
    form = dict(parse_qsl(urlsplit(authorize).query))
    answer = site.provider.client.post(
        authorize.split("?")[0], data=form | {"username": username, "password": password}
    )
    unreachable(site, monkeypatch)
    done = site.browser.get(answer.headers["location"])
    assert done.status_code == 302 and done.headers["location"] == "/?signIn=failed"


def test_an_unreachable_provider_at_the_start_fails_the_sign_in(site, monkeypatch) -> None:
    unreachable(site, monkeypatch)
    start = site.browser.get("/api/auth/login", params={"returnTo": "/after"})
    assert start.status_code == 302 and start.headers["location"] == "/?signIn=failed"
    assert not site.browser.cookies.get("__Host-craft_signin")


def test_sign_out_clears_the_cookie_with_the_provider_unreachable(
    site, owner_engine, monkeypatch
) -> None:
    username, password, _ = site.provider.account(email=f"{uuid4().hex[:8]}@example.org")
    with site.provider.sessions() as session:
        email = identity.storage.account(session, username)["email"]
    craft_user(site, owner_engine, email)
    assert sign_in(site, username, password).headers["location"] == "/after"
    unreachable(site, monkeypatch)

    out = site.browser.post(
        "/api/auth/logout", headers={"Origin": site.settings.allowed_origins.split(",")[0]}
    )

    assert out.status_code == 200 and out.json() == {"logoutUrl": None}
    cookie = out.headers["set-cookie"]
    assert cookie.startswith("__Host-craft_session=") and "Max-Age=0" in cookie
