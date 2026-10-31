"""Sign in at craft-id, see your account, sign out: the browser's whole round trip."""

from urllib.parse import parse_qsl, urlencode, urlsplit

import msgspec
import pytest

from app.accounts import oidc, storage
from app.tests.support.accounts import SESSION_COOKIE
from app.tests.support.identity import PUBLIC, REDIRECT, Provider, keys, provider  # noqa: F401


@pytest.fixture
def craft_id(journey, provider: Provider, monkeypatch):  # noqa: F811
    """The app signs people in at an in-process craft-id, where the journey's person has an
    account with their email; returns that account's username and password."""
    monkeypatch.setattr(oidc, "http", provider.client)
    monkeypatch.setattr(oidc, "_metadata", {})
    monkeypatch.setattr(oidc, "_keys", {})
    journey.app.state.settings = msgspec.structs.replace(
        journey.app.state.settings,
        oidc_authority=provider.authority,
        oidc_client_id=provider.client_id,
        oidc_client_secret=provider.secret,
        oidc_redirect_uri=REDIRECT,
    )
    email = f"{journey.person.username}@example.org"
    with journey.owner.begin() as connection:
        connection.execute(
            storage.users.update()
            .where(storage.users.c.id == journey.person.id)
            .values(email=email)
        )
    username, password, _ = provider.account(email=email)
    return username, password


def sign_in_at(journey, provider: Provider, username: str, password: str) -> None:  # noqa: F811
    """Go to craft-id's form, sign in there and come back with a session cookie."""
    start = journey.http.get("/api/auth/login", params={"returnTo": "/after"})
    assert start.status_code == 302
    authorize = start.headers["location"]
    assert provider.client.get(authorize).status_code == 200  # the form
    form = dict(parse_qsl(urlsplit(authorize).query))
    answer = provider.client.post(
        authorize.split("?")[0], data=form | {"username": username, "password": password}
    )
    assert answer.status_code == 302 and answer.headers["location"].startswith(REDIRECT)
    back = journey.http.get(answer.headers["location"])
    assert (back.status_code, back.headers["location"]) == (302, "/after")


def test_a_person_signs_in_at_craft_id_sees_their_account_and_signs_out(
    journey,
    provider: Provider,  # noqa: F811
    craft_id,
) -> None:
    http, person = journey.http, journey.person
    assert http.get("/api/me").status_code == 401

    sign_in_at(journey, provider, *craft_id)
    me = http.get("/api/me")
    assert me.status_code == 200
    assert me.json() == {
        "id": str(person.id),
        "username": person.username,
        "displayName": "Generated person",
        "roles": ["member"],
        "language": "en",
        "email": f"{person.username}@example.org",
    }

    cookie = http.cookies.get(SESSION_COOKIE)
    out = http.post("/api/auth/logout")
    assert out.status_code == 200
    assert out.json() == {
        "logoutUrl": provider.authority.removesuffix("/v2.0")
        + "/oauth2/v2.0/logout?"
        + urlencode({"post_logout_redirect_uri": PUBLIC + "/", "client_id": provider.client_id})
    }
    assert http.get("/api/me").status_code == 401
    http.cookies.set(SESSION_COOKIE, cookie)  # the server ended the session, not just the browser
    assert http.get("/api/me").status_code == 401
    http.cookies.clear()

    # Signing in again links by the provider's account; DELETE /api/session also signs out.
    sign_in_at(journey, provider, *craft_id)
    assert http.get("/api/me").status_code == 200
    assert http.delete("/api/session").status_code == 204
    assert http.get("/api/me").status_code == 401
