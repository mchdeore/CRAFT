"""craft-id in-process, with a generated key, reached at http://testserver."""

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from app.identity import service
from app.identity.handlers import create_identity_app
from app.identity.settings import IdentitySettings

PUBLIC = "https://testserver"
REDIRECT = "https://testserver/api/auth/callback"


class Http:
    """The requests-like client MSAL expects, over the in-process app."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def get(self, url, params=None, headers=None, **_):
        return self.client.get(url, params=params, headers=headers)

    def post(self, url, params=None, data=None, headers=None, **_):
        return self.client.post(url, params=params, data=data, headers=headers)

    def close(self) -> None:
        pass


@dataclass
class Provider:
    client: TestClient
    http: Http
    sessions: sessionmaker
    keys: service.Keys
    client_id: str
    secret: str
    authority: str

    def account(self, password: str = "a-long-test-password", email: str | None = None):
        username = f"user-{uuid4().hex[:8]}"
        oid = service.add_account(self.sessions, username, username.title(), email, password)
        return username, password, oid


@pytest.fixture(scope="session")
def keys() -> service.Keys:
    return service.Keys()


@pytest.fixture
def provider(owner_engine: Engine, keys: service.Keys) -> Iterator[Provider]:
    # The owner login stands in for craft_id here; test_grants checks craft_id's own grants.
    sessions = sessionmaker(owner_engine, expire_on_commit=False)
    settings = IdentitySettings(id_public_url=PUBLIC)
    client_id, secret = service.add_client(sessions, "craft-app", [REDIRECT, PUBLIC + "/"])
    with TestClient(
        create_identity_app(settings, sessions, keys), base_url=PUBLIC, follow_redirects=False
    ) as client:
        yield Provider(
            client, Http(client), sessions, keys, str(client_id), secret, service.issuer(PUBLIC)
        )
