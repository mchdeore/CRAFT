"""Workspace tokens reach the read routes as their account, logged as workspace calls."""

import base64
import hashlib
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import create_app
from app.accounts import service, storage
from app.infrastructure import access
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.operations.registry import registry
from app.infrastructure.operations.storage import call_log
from app.infrastructure.shapes import Shape

PASSWORD = "synthetic-workspace-password"
ORIGIN = "http://localhost:8000"


class Nested(Shape):
    owner: str


@pytest.fixture
def account(settings, owner_engine):
    snapshot = registry.copy()
    service.ATTEMPTS.clear()

    @operation(SIGNED_IN)
    def internal_probe(caller: Caller) -> Nested:
        return Nested(owner=str(caller.user_id))

    @operation(SIGNED_IN, get="/workspace-nested-probe", tool=True)
    def workspace_nested_probe(caller: Caller) -> Nested:
        """Return the caller through an internal operation; for example a nested read."""
        return internal_probe(caller)

    username = "workspace_" + uuid4().hex
    with owner_engine.begin() as connection:
        user = connection.execute(
            storage.users.insert()
            .values(username=username, display_name="Test")
            .returning(storage.users.c.id)
        ).scalar_one()
        connection.execute(
            storage.local_credentials.insert().values(
                user_id=user, password_hash=service.PASSWORDS.hash(PASSWORD)
            )
        )
        connection.execute(storage.user_roles.insert().values(user_id=user, role="member"))
    app = create_app(settings)
    with (
        TestClient(app, base_url="https://testserver", headers={"Origin": ORIGIN}) as browser,
        TestClient(app, base_url="https://testserver") as python,
    ):
        signed_in = browser.post("/api/session", json={"username": username, "password": PASSWORD})
        assert signed_in.status_code == 200
        yield SimpleNamespace(id=user, browser=browser, python=python)
    registry.clear()
    registry.update(snapshot)
    with owner_engine.begin() as connection:
        connection.execute(call_log.delete().where(call_log.c.user_id == user))
        for table in [
            storage.workspace_tokens,
            storage.auth_sessions,
            storage.local_credentials,
            storage.user_roles,
        ]:
            connection.execute(table.delete().where(table.c.user_id == user))
        connection.execute(storage.users.delete().where(storage.users.c.id == user))


def issue(account, **body):
    response = account.browser.post("/api/me/workspace-tokens", json={"label": "notebook", **body})
    assert response.status_code == 200, response.text
    return response.json()


def bearer(token):
    return {"Authorization": "Bearer " + token}


def test_token_is_shown_once_and_stored_as_a_digest(account, owner_engine):
    issued = issue(account, hours=2)
    raw = base64.urlsafe_b64decode(issued["token"] + "=" * ((-len(issued["token"])) % 4))
    assert len(raw) == 32
    listed = account.browser.get("/api/me/workspace-tokens").json()
    assert [row["id"] for row in listed] == [issued["id"]]
    assert "token" not in listed[0]
    with owner_engine.connect() as connection:
        stored = connection.execute(
            select(storage.workspace_tokens.c.token_sha256).where(
                storage.workspace_tokens.c.id == issued["id"]
            )
        ).scalar_one()
    assert stored == hashlib.sha256(raw).digest()


def test_token_reads_as_its_account_and_is_logged_as_workspace(account, owner_engine):
    issued = issue(account)
    me = account.python.get("/api/me", headers=bearer(issued["token"]))
    assert me.status_code == 200 and me.json()["id"] == str(account.id)
    parameters = account.python.get("/api/cube/parameters", headers=bearer(issued["token"]))
    assert parameters.status_code == 200
    search = account.python.post(
        "/api/library/search", json={"query": "anything"}, headers=bearer(issued["token"])
    )
    assert search.status_code == 200
    with owner_engine.connect() as connection:
        rows = (
            connection.execute(
                select(call_log.c.name, call_log.c.origin, call_log.c.workspace_token_id)
                .where(call_log.c.user_id == account.id, call_log.c.origin == "workspace")
                .order_by(call_log.c.id)
            )
            .mappings()
            .all()
        )
    names = [row["name"] for row in rows]
    # Search embeds its query, and those model calls are logged under the same token.
    assert names == [
        "accounts.me",
        "cube.list_parameters",
        "agent.embedding",
        "agent.embed",
        "library.search",
    ]
    assert {str(row["workspace_token_id"]) for row in rows} == {issued["id"]}


def test_token_cannot_reach_writes_or_token_management(account):
    token = issue(account)["token"]
    refused = [
        account.python.post("/api/me/workspace-tokens", json={"label": "x"}, headers=bearer(token)),
        account.python.get("/api/me/workspace-tokens", headers=bearer(token)),
        account.python.delete("/api/session", headers=bearer(token)),
        account.python.put("/api/notes", json={"title": "x", "body": "y"}, headers=bearer(token)),
        account.python.get("/api/conversations", headers=bearer(token)),
    ]
    assert [response.status_code for response in refused] == [403] * len(refused)


def test_token_with_a_browser_cookie_is_refused(account):
    token = issue(account)["token"]
    assert account.browser.get("/api/me").status_code == 200
    assert account.browser.get("/api/me", headers=bearer(token)).status_code == 401


def test_revoked_expired_and_disabled_tokens_stop_working(account, owner_engine):
    revoked = issue(account)
    expired = issue(account)
    kept = issue(account)
    assert account.browser.delete(f"/api/me/workspace-tokens/{revoked['id']}").status_code == 204
    assert account.browser.delete(f"/api/me/workspace-tokens/{uuid4()}").status_code == 404
    with owner_engine.begin() as connection:
        connection.execute(
            update(storage.workspace_tokens)
            .where(storage.workspace_tokens.c.id == expired["id"])
            .values(expires_at=access.now() - timedelta(seconds=1))
        )
    statuses = [
        account.python.get("/api/me", headers=bearer(row["token"])).status_code
        for row in (revoked, expired, kept)
    ]
    assert statuses == [401, 401, 200]
    with owner_engine.begin() as connection:
        connection.execute(
            update(storage.users)
            .where(storage.users.c.id == account.id)
            .values(disabled_at=access.now())
        )
    assert account.python.get("/api/me", headers=bearer(kept["token"])).status_code == 401


def test_malformed_bearer_is_not_signed_in(account):
    for header in ["Bearer ", "Bearer not-base64!", "Basic abc", "Bearer " + "A" * 43]:
        response = account.python.get("/api/me", headers={"Authorization": header})
        assert response.status_code == 401


def test_read_tool_may_use_internal_operations(account):
    token = issue(account)["token"]
    nested = account.python.get("/api/workspace-nested-probe", headers=bearer(token))
    assert nested.status_code == 200 and nested.json() == {"owner": str(account.id)}


def hours(row):
    from datetime import datetime

    span = datetime.fromisoformat(row["expiresAt"]) - datetime.fromisoformat(row["createdAt"])
    return span / timedelta(hours=1)


def test_kinds_have_their_own_default_and_longest_lifetime(account):
    def create(**body):
        return account.browser.post("/api/me/tokens", json={"label": "client", **body})

    workspace = create()
    assert workspace.status_code == 200
    assert workspace.json()["kind"] == "workspace" and hours(workspace.json()) == 12
    mcp = create(kind="mcp")
    assert mcp.status_code == 200 and mcp.json()["kind"] == "mcp" and hours(mcp.json()) == 168
    assert hours(create(kind="mcp", hours=720).json()) == 720
    assert hours(create(hours=168).json()) == 168
    for body in [
        {"hours": 169},
        {"kind": "mcp", "hours": 721},
        {"kind": "mcp", "hours": 0},
        {"kind": "other"},
    ]:
        refused = create(**body)
        assert refused.status_code == 400 and refused.json()["code"] == "invalid_input", body
    assert create(hours=169).json()["fields"] == [{"path": "$.hours", "message": "At most 168."}]


def test_the_table_refuses_a_longer_lifetime_than_its_kind_allows(account, owner_engine):
    from sqlalchemy.exc import IntegrityError

    at = access.now()
    for kind, allowed in [("workspace", 168), ("mcp", 720)]:
        with owner_engine.begin() as connection:
            connection.execute(
                storage.workspace_tokens.insert().values(
                    user_id=account.id,
                    token_sha256=uuid4().bytes * 2,
                    kind=kind,
                    label="direct",
                    created_at=at,
                    expires_at=at + timedelta(hours=allowed),
                )
            )
        with pytest.raises(IntegrityError), owner_engine.begin() as connection:
            connection.execute(
                storage.workspace_tokens.insert().values(
                    user_id=account.id,
                    token_sha256=uuid4().bytes * 2,
                    kind=kind,
                    label="direct",
                    created_at=at,
                    expires_at=at + timedelta(hours=allowed, seconds=1),
                )
            )
    with pytest.raises(IntegrityError), owner_engine.begin() as connection:
        connection.execute(
            storage.workspace_tokens.insert().values(
                user_id=account.id,
                token_sha256=uuid4().bytes * 2,
                kind="other",
                label="direct",
                expires_at=at,
            )
        )


def test_both_kinds_are_listed_and_revoked_under_either_name(account):
    workspace = issue(account)
    mcp = account.browser.post("/api/me/tokens", json={"label": "client", "kind": "mcp"}).json()
    listed = account.browser.get("/api/me/tokens").json()
    assert [(row["id"], row["kind"]) for row in listed] == [
        (mcp["id"], "mcp"),
        (workspace["id"], "workspace"),
    ]
    assert account.browser.get("/api/me/workspace-tokens").json() == listed
    assert all("token" not in row for row in listed)
    assert account.browser.delete(f"/api/me/workspace-tokens/{mcp['id']}").status_code == 204
    assert account.browser.delete(f"/api/me/tokens/{workspace['id']}").status_code == 204
    assert all(row["revokedAt"] for row in account.browser.get("/api/me/tokens").json())
    assert account.browser.delete(f"/api/me/tokens/{uuid4()}").status_code == 404


def test_an_mcp_token_never_signs_in_at_the_api(account, owner_engine):
    mcp = account.browser.post("/api/me/tokens", json={"label": "client", "kind": "mcp"}).json()
    for method, path in [
        ("GET", "/api/me"),
        ("GET", "/api/cube/parameters"),
        ("GET", "/api/me/tokens"),
        ("GET", "/api/me/workspace-tokens"),
        ("GET", "/api/workspace-nested-probe"),
    ]:
        response = account.python.request(method, path, headers=bearer(mcp["token"]))
        assert response.status_code == 401, path
    with owner_engine.connect() as connection:
        used = connection.execute(
            select(storage.workspace_tokens.c.last_used_at).where(
                storage.workspace_tokens.c.id == mcp["id"]
            )
        ).scalar_one()
        logged = connection.execute(
            select(call_log.c.id).where(call_log.c.workspace_token_id == mcp["id"])
        ).all()
    assert used is None and logged == []


def test_tokens_cannot_issue_tokens_of_either_kind(account):
    workspace = issue(account)["token"]
    for kind in ["workspace", "mcp"]:
        response = account.python.post(
            "/api/me/tokens", json={"label": "x", "kind": kind}, headers=bearer(workspace)
        )
        assert response.status_code == 403


def test_a_workspace_token_cannot_ask_the_agent(account):
    token = issue(account)["token"]
    asked = account.python.post(
        "/api/agent/ask", json={"question": "Peak power"}, headers=bearer(token)
    )
    assert asked.status_code == 403 and asked.json()["code"] == "forbidden"


# The swap: a workspace JupyterHub started trades its server's Hub token for a workspace token.
SERVER_TOKEN = "generated0hub0server0token0a"
NEW_SERVER_TOKEN = "generated0hub0server0token0b"


class FakeHub:
    """Stands in for JupyterHub's answer to "whose server holds this token?"."""

    def __init__(self, owners, down=False):
        self.owners, self.down, self.asked = owners, down, []

    async def owner(self, token):
        self.asked.append(token)
        if self.down:
            import httpx

            raise httpx.ConnectError("hub unreachable")
        return self.owners.get(token)


@pytest.fixture
def hub_says(monkeypatch):
    from app.infrastructure import hub

    def install(owners, **options):
        fake = FakeHub(owners, **options)
        monkeypatch.setattr(hub, "from_settings", lambda _: fake)
        return fake

    return install


def swap(account, token, scheme="token "):
    # No Origin header: a kernel isn't a browser.
    headers = {"Authorization": scheme + token}
    return account.python.post("/api/workspace-tokens/hub", headers=headers)


def test_a_server_token_swaps_for_a_read_only_twelve_hour_token(account, hub_says, owner_engine):
    from datetime import datetime

    hub_says({SERVER_TOKEN: account.id})
    swapped = swap(account, SERVER_TOKEN)
    assert swapped.status_code == 200, swapped.text
    issued = swapped.json()
    assert issued["label"] == service.HUB_TOKEN_LABEL
    lasts = datetime.fromisoformat(issued["expiresAt"]) - datetime.fromisoformat(
        issued["createdAt"]
    )
    assert lasts == timedelta(hours=service.HUB_TOKEN_HOURS)
    me = account.python.get("/api/me", headers=bearer(issued["token"]))
    assert me.status_code == 200 and me.json()["id"] == str(account.id)
    refused = [
        account.python.get("/api/conversations", headers=bearer(issued["token"])),
        account.python.post(
            "/api/me/workspace-tokens", json={"label": "x"}, headers=bearer(issued["token"])
        ),
        account.python.put(
            "/api/notes", json={"title": "x", "body": "y"}, headers=bearer(issued["token"])
        ),
    ]
    assert [response.status_code for response in refused] == [403, 403, 403]
    with owner_engine.connect() as connection:
        logged = connection.execute(
            select(call_log.c.user_id, call_log.c.outcome).where(
                call_log.c.name == "accounts.workspace_token_from_hub",
                call_log.c.user_id == account.id,
            )
        ).all()
    assert [tuple(row) for row in logged] == [(account.id, "ok")]  # the audit names the account


def test_the_swap_refuses_any_token_the_hub_does_not_vouch_for(account, hub_says, owner_engine):
    stranger = uuid4()  # a Hub user with no CRAFT account
    fake = hub_says({SERVER_TOKEN: account.id, "generated0stranger0token00": stranger})
    statuses = [
        swap(account, "generated0unknown0token000").status_code,
        swap(account, "generated0stranger0token00").status_code,
        swap(account, "short").status_code,
        swap(account, SERVER_TOKEN + " extra").status_code,
        swap(account, SERVER_TOKEN, scheme="Bearer ").status_code,
        swap(account, SERVER_TOKEN, scheme="Token ").status_code,
    ]
    # Another scheme isn't a token at all, so the missing Origin refuses it first.
    assert statuses == [401] * 5 + [403]
    # Malformed tokens and other schemes are refused before anything is sent to the Hub.
    assert fake.asked == ["generated0unknown0token000", "generated0stranger0token00"]
    with owner_engine.connect() as connection:
        made = connection.execute(
            select(storage.workspace_tokens.c.id).where(
                storage.workspace_tokens.c.user_id == stranger
            )
        ).all()
    assert made == []


def test_a_disabled_account_or_a_deployment_without_a_hub_gets_no_token(
    account, hub_says, owner_engine, monkeypatch
):
    from app.infrastructure import hub

    hub_says({SERVER_TOKEN: account.id})
    with owner_engine.begin() as connection:
        connection.execute(
            update(storage.users)
            .where(storage.users.c.id == account.id)
            .values(disabled_at=access.now())
        )
    assert swap(account, SERVER_TOKEN).status_code == 401
    monkeypatch.setattr(hub, "from_settings", lambda _: None)
    assert swap(account, SERVER_TOKEN).status_code == 401


def test_a_hub_that_is_down_is_an_internal_error(account, hub_says):
    hub_says({}, down=True)
    failed = swap(account, SERVER_TOKEN)
    assert failed.status_code == 500 and failed.json()["code"] == "internal_error"
    assert SERVER_TOKEN not in failed.text


def test_after_a_restart_only_the_new_server_token_swaps(account, hub_says, owner_engine):
    hub_says({SERVER_TOKEN: account.id})
    first = swap(account, SERVER_TOKEN).json()
    # The Hub revokes a server's token when the server stops; the new server has its own.
    hub_says({NEW_SERVER_TOKEN: account.id})
    assert swap(account, SERVER_TOKEN).status_code == 401
    second = swap(account, NEW_SERVER_TOKEN).json()
    assert second["token"] != first["token"]
    # Each issued token still ends like any workspace token: revoked, or expired.
    assert account.browser.delete(f"/api/me/workspace-tokens/{first['id']}").status_code == 204
    assert account.python.get("/api/me", headers=bearer(first["token"])).status_code == 401
    with owner_engine.begin() as connection:
        connection.execute(
            update(storage.workspace_tokens)
            .where(storage.workspace_tokens.c.id == second["id"])
            .values(expires_at=access.now() - timedelta(seconds=1))
        )
    assert account.python.get("/api/me", headers=bearer(second["token"])).status_code == 401


def test_a_token_header_never_lets_a_foreign_page_write(account):
    # A browser can't add these headers cross-site, and a cookie with one is anonymous anyway.
    foreign = {"Origin": "https://elsewhere.example"}
    for header in ({}, {"Authorization": "token " + SERVER_TOKEN}, bearer(SERVER_TOKEN)):
        made = account.browser.post(
            "/api/me/workspace-tokens", json={"label": "x"}, headers={**foreign, **header}
        )
        assert made.status_code in {401, 403}, header
    # Cached Basic credentials are added by browsers, so they never skip the Origin check.
    basic = {"Authorization": "Basic Z2VuZXJhdGVkOmdlbmVyYXRlZA=="}
    assert account.python.post("/api/workspace-tokens/hub", headers=basic).status_code == 403
    assert account.python.post("/api/workspace-tokens/hub").status_code == 403


def test_no_token_reaches_a_log_an_error_body_or_the_call_log(
    account, hub_says, owner_engine, caplog
):
    import logging

    caplog.set_level(logging.DEBUG)
    hub_says({SERVER_TOKEN: account.id})
    responses = [swap(account, SERVER_TOKEN), swap(account, NEW_SERVER_TOKEN)]
    issued = responses[0].json()["token"]
    responses.append(account.python.get("/api/me", headers=bearer(issued)))
    hub_says({}, down=True)
    responses.append(swap(account, SERVER_TOKEN))
    secrets_ = [SERVER_TOKEN, NEW_SERVER_TOKEN, issued]
    logged = "\n".join(repr(record.__dict__) for record in caplog.records)
    bodies = "\n".join(response.text for response in responses[1:])
    with owner_engine.connect() as connection:
        rows = repr(
            connection.execute(select(call_log).where(call_log.c.user_id == account.id)).all()
        )
    for secret in secrets_:
        assert secret not in logged and secret not in bodies and secret not in rows
