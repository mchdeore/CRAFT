"""craft-id in-process, with a generated key, reached at https://testserver."""

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, or_
from sqlalchemy.orm import sessionmaker

from app.identity import service, storage
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
    accounts: list[UUID]  # the oids account() made, for the fixture to delete

    def account(self, password: str = "a-long-test-password", email: str | None = None):
        username = f"user-{uuid4().hex[:8]}"
        oid = service.add_account(self.sessions, username, username.title(), email, password)
        self.accounts.append(oid)
        return username, password, oid


@pytest.fixture(scope="session")
def keys() -> service.Keys:
    return service.Keys()


@pytest.fixture
def provider(owner_engine: Engine, keys: service.Keys) -> Iterator[Provider]:
    """craft-id with the client "craft-app" registered. At teardown the client and the accounts
    made with Provider.account are deleted, with their credentials and codes."""
    # The owner login stands in for craft_id here; test_grants checks craft_id's own grants.
    sessions = sessionmaker(owner_engine, expire_on_commit=False)
    settings = IdentitySettings(id_public_url=PUBLIC)
    client_id, secret = service.add_client(sessions, "craft-app", [REDIRECT, PUBLIC + "/"])
    accounts: list[UUID] = []
    try:
        with TestClient(
            create_identity_app(settings, sessions, keys), base_url=PUBLIC, follow_redirects=False
        ) as client:
            authority = service.issuer(PUBLIC)
            yield Provider(
                client, Http(client), sessions, keys, str(client_id), secret, authority, accounts
            )
    finally:
        remove(owner_engine, client_id, accounts)


def remove(engine: Engine, client: UUID, accounts: list[UUID]) -> None:
    """Delete the client and the accounts with the codes and credentials that refer to them,
    each table's rows before the rows they refer to."""
    codes, credentials = storage.id_codes, storage.id_credentials
    with engine.begin() as connection:
        for table, rows in [
            (codes, or_(codes.c.client_id == client, codes.c.account_id.in_(accounts))),
            (credentials, credentials.c.account_id.in_(accounts)),
            (storage.id_accounts, storage.id_accounts.c.id.in_(accounts)),
            (storage.id_clients, storage.id_clients.c.id == client),
        ]:
            connection.execute(table.delete().where(rows))
