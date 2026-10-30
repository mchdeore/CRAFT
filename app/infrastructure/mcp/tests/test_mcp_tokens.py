"""Outside MCP clients sign in at /mcp with a personal `mcp` token, as its account, read-only."""

import asyncio
import base64
import hashlib
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app import create_app
from app.agent import service as agent_service
from app.agent.handlers import RunRef, issue_run_token, revoke_run_token
from app.agent.tests.test_chat_stream import request
from app.infrastructure import access
from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.mcp.tests.test_mcp import session_stream
from app.infrastructure.operations import DATA, operation
from app.infrastructure.operations.registry import registry
from app.infrastructure.operations.storage import call_log
from app.infrastructure.shapes import Shape
from app.tests.support.chat import chat as chat
from app.tests.support.cube import cube_source as cube_source

tokens = m.metadata.tables["workspace_tokens"]
users = m.metadata.tables["users"]


@pytest.fixture
def account(chat):
    """The chat account's browser session, which issues its tokens."""
    app = create_app(chat["settings"])
    with (
        TestClient(
            app, base_url="https://testserver", headers={"Origin": "https://testserver"}
        ) as browser,
        TestClient(app, base_url="https://testserver") as python,
    ):
        assert chat["sign_in"](browser).status_code == 200

        def issue(kind="mcp", **body):
            response = browser.post(
                "/api/me/tokens", json={"label": "client", "kind": kind, **body}
            )
            assert response.status_code == 200, response.text
            return response.json()

        yield chat | {
            "browser": browser,
            "python": python,
            "issue": issue,
            "user": chat["caller"].user_id,
        }
    with chat["owner"].begin() as connection:
        connection.execute(call_log.delete().where(call_log.c.user_id == chat["caller"].user_id))
        connection.execute(tokens.delete().where(tokens.c.user_id == chat["caller"].user_id))


def post(account, raw, method="tools/list", params=None, **headers):
    """One JSON-RPC message to /mcp, as a client without the SDK sends it."""
    sent = {"Accept": "application/json, text/event-stream", **headers}
    if raw is not None:
        sent["Authorization"] = "Bearer " + raw
    with httpx.Client(trust_env=False) as client:
        return client.post(
            account["settings"].mcp_url,
            headers=sent,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        )


def call(account, raw, name, arguments=None, **headers):
    return post(account, raw, "tools/call", {"name": name, "arguments": arguments or {}}, **headers)


def listed(account, raw):
    async def execute():
        async with session_stream(account["settings"].mcp_url, raw) as session:
            return {tool.name for tool in (await session.list_tools()).tools}

    return asyncio.run(execute())


def logged(account, **where):
    with account["owner"].connect() as connection:
        return (
            connection.execute(
                select(call_log)
                .where(*[call_log.c[name] == value for name, value in where.items()])
                .order_by(call_log.c.id)
            )
            .mappings()
            .all()
        )


def test_mcp_token_lists_read_tools_and_calls_them_as_its_account(account, capsys):
    issued = account["issue"]()
    names = listed(account, issued["token"])
    reads = {o.tool_name for o in registry.values() if o.tool and o.effects == "NONE"}
    assert names == reads
    assert "ask_agent" in names
    assert not {o.tool_name for o in registry.values() if o.tool and o.effects != "NONE"} & names

    result = call(account, issued["token"], "list_missions")
    assert result.status_code == 200
    body = result.json()["result"]
    assert body["isError"] is False
    assert [row["key"] for row in body["structuredContent"]["items"]] == [account["mission"]]

    rows = logged(account, workspace_token_id=issued["id"])
    assert [(row["name"], row["origin"], row["outcome"]) for row in rows] == [
        ("cube.list_missions", "mcp", "ok")
    ]
    assert rows[0]["user_id"] == account["user"] and rows[0]["run_id"] is None
    assert rows[0]["parent_call_id"] is None and rows[0]["auth_session_id"] is None
    captured = capsys.readouterr()
    assert issued["token"] not in captured.out + captured.err
    with account["owner"].connect() as connection:
        stored = connection.execute(
            select(tokens.c.kind, tokens.c.last_used_at).where(tokens.c.id == issued["id"])
        ).one()
    assert stored.kind == "mcp" and stored.last_used_at is not None


def test_effectful_tools_are_unlisted_refused_and_never_run(account):
    executed = []

    class EffectInput(Shape):
        value: int

    @operation(PUBLIC, post="/mcp-token-effect-probe", tool=True, effects=DATA)
    def token_effect_probe(caller: Caller, request: EffectInput) -> None:
        """Attempt one simulated effect; use to test refusal, for example value1."""
        executed.append(True)

    try:
        raw = account["issue"]()["token"]
        assert "token_effect_probe" not in listed(account, raw)
        effectful = sorted(o.tool_name for o in registry.values() if o.tool and o.effects != "NONE")
        assert "token_effect_probe" in effectful and len(effectful) > 1
        for name in effectful:
            result = call(account, raw, name, {"value": 1})
            assert result.status_code == 200, name
            body = result.json()["result"]
            assert body["isError"] is True and body["structuredContent"]["code"] == "forbidden", (
                name
            )
        assert not executed
        refused = logged(account, user_id=account["user"], origin="mcp")
        assert {row["error_code"] for row in refused} == {"forbidden"}
        assert len(refused) == len(effectful)
    finally:
        registry.pop(token_effect_probe.__operation__.name)


def test_each_endpoint_refuses_the_other_kinds(account):
    mcp = account["issue"]()["token"]
    workspace = account["issue"]("workspace")["token"]
    api = account["python"]
    # An MCP token never signs in at /api, on reads, the account check or token management.
    for method, path, body in [
        ("GET", "/api/me", None),
        ("GET", "/api/cube/parameters", None),
        ("POST", "/api/library/search", {"query": "anything"}),
        ("GET", "/api/me/tokens", None),
        ("POST", "/api/me/tokens", {"label": "x", "kind": "mcp"}),
    ]:
        response = api.request(method, path, json=body, headers={"Authorization": "Bearer " + mcp})
        assert response.status_code == 401, path
    assert api.get("/api/me", headers={"Authorization": "Bearer " + workspace}).status_code == 200
    # A workspace token never signs in at /mcp.
    refused = post(account, workspace)
    assert refused.status_code == 401
    assert refused.headers["WWW-Authenticate"] == "Bearer"
    assert refused.json()["code"] == "not_signed_in"
    assert post(account, mcp).status_code == 200


def test_run_tokens_are_not_personal_tokens(account):
    data = request(account)
    agent_service.start(account["caller"], data, "simulated")
    trusted = replace(account["caller"], conversation_id=data.thread_id, run_id=data.run_id)
    raw = issue_run_token(trusted, RunRef(run=data.run_id)).token
    try:
        api = account["python"]
        assert api.get("/api/me", headers={"Authorization": "Bearer " + raw}).status_code == 401
        # Unchanged at /mcp: the full catalog, and X-Craft-User is ignored.
        names = listed(account, raw)
        assert names == {o.tool_name for o in registry.values() if o.tool}
        assert post(account, raw, **{"X-Craft-User": "someone-else"}).status_code == 200
    finally:
        revoke_run_token(trusted, RunRef(run=data.run_id))
        agent_service.finish(trusted, data, "cancelled", {})


def test_revoked_expired_and_disabled_tokens_stop_at_the_next_call(account):
    revoked, expired, kept = account["issue"](), account["issue"](), account["issue"]()
    for issued in (revoked, expired, kept):
        assert call(account, issued["token"], "list_parameters").status_code == 200
    assert account["browser"].delete(f"/api/me/tokens/{revoked['id']}").status_code == 204
    with account["owner"].begin() as connection:
        connection.execute(
            update(tokens)
            .where(tokens.c.id == expired["id"])
            .values(expires_at=access.now() - timedelta(seconds=1))
        )
    statuses = [post(account, issued["token"]).status_code for issued in (revoked, expired, kept)]
    assert statuses == [401, 401, 200]
    with account["owner"].begin() as connection:
        connection.execute(
            update(users).where(users.c.id == account["user"]).values(disabled_at=access.now())
        )
    try:
        assert post(account, kept["token"]).status_code == 401
        assert call(account, kept["token"], "list_parameters").status_code == 401
    finally:
        with account["owner"].begin() as connection:
            connection.execute(
                update(users).where(users.c.id == account["user"]).values(disabled_at=None)
            )
    assert post(account, kept["token"]).status_code == 200


def test_malformed_and_unknown_bearers_are_not_signed_in(account):
    unknown = hashlib.sha256(uuid4().bytes).digest()
    for header in [
        None,
        "Basic abc",
        "Bearer not-base64!",
        "Bearer " + "A" * 43,
        "Bearer " + base64.urlsafe_b64encode(unknown).rstrip(b"=").decode(),
    ]:
        sent = {"Accept": "application/json, text/event-stream"}
        if header is not None:
            sent["Authorization"] = header
        with httpx.Client(trust_env=False) as client:
            response = client.post(
                account["settings"].mcp_url,
                headers=sent,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
        assert response.status_code == 401, header


def test_x_craft_user_must_name_the_tokens_account(account):
    raw = account["issue"]()["token"]
    assert post(account, raw, **{"X-Craft-User": account["username"]}).status_code == 200
    refused = post(account, raw, **{"X-Craft-User": "someone-else"})
    assert refused.status_code == 401 and refused.json()["code"] == "not_signed_in"
    assert call(account, raw, "list_parameters", **{"X-Craft-User": ""}).status_code == 401
    assert post(account, raw).status_code == 200


def test_mission_grants_apply_and_are_reread_on_each_call(account):
    raw = account["issue"]()["token"]
    hidden = "msn-" + uuid4().hex
    with account["owner"].begin() as connection:
        connection.execute(
            m.missions.insert().values(
                key=hidden, title="Ungranted mission", current_stage="st3-design", simulated=True
            )
        )

    def keys():
        body = call(account, raw, "list_missions", {"limit": 100}).json()["result"]
        return {row["key"] for row in body["structuredContent"]["items"]}

    def state(key):
        return call(account, raw, "get_mission", {"key": key}).json()["result"]

    try:
        assert keys() == {account["mission"]}
        assert state(hidden)["structuredContent"]["code"] == "not_found"
        assert state(account["mission"])["isError"] is False
        with account["owner"].begin() as connection:
            connection.execute(
                m.mission_grants.insert().values(
                    user_id=account["user"], mission_key=hidden, silo=None
                )
            )
        assert keys() == {account["mission"], hidden}
        assert state(hidden)["isError"] is False
        with account["owner"].begin() as connection:
            connection.execute(
                m.mission_grants.delete().where(
                    m.mission_grants.c.user_id == account["user"],
                    m.mission_grants.c.mission_key == hidden,
                )
            )
        assert keys() == {account["mission"]}
    finally:
        with account["owner"].begin() as connection:
            connection.execute(
                m.mission_grants.delete().where(m.mission_grants.c.mission_key == hidden)
            )
            connection.execute(m.missions.delete().where(m.missions.c.key == hidden))


def add_calls(account, token, count, age=0, parent=None):
    """Logged calls `age` seconds old by the database clock, which the limit also uses."""
    with account["owner"].begin() as connection:
        connection.execute(
            call_log.insert().values(at=func.now() - timedelta(seconds=age)),
            [
                {
                    "kind": "operation",
                    "name": "cube.list_parameters",
                    "origin": "mcp",
                    "outcome": "ok",
                    "duration_ms": 1,
                    "request_id": uuid4(),
                    "call_id": uuid4(),
                    "parent_call_id": parent,
                    "user_id": account["user"],
                    "workspace_token_id": token,
                }
                for _ in range(count)
            ],
        )


def test_sixty_tool_calls_a_minute_per_token(account):
    issued, other = account["issue"](), account["issue"]()
    # Older calls and nested calls do not count; 59 recent calls leave room for one more.
    add_calls(account, issued["id"], 200, age=61)
    add_calls(account, issued["id"], 20, parent=uuid4())
    add_calls(account, issued["id"], 9, age=50)
    add_calls(account, issued["id"], 50)
    assert call(account, issued["token"], "list_parameters").status_code == 200
    limited = post(account, issued["token"])
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited" and "requestId" in limited.json()
    # The oldest counted call is 50 seconds old, so one slot frees in about 10 seconds.
    assert 8 <= int(limited.headers["Retry-After"]) <= 11
    assert "WWW-Authenticate" not in limited.headers
    assert call(account, issued["token"], "list_parameters").status_code == 429
    # The limit is per token, and a refused request is not a call.
    assert post(account, other["token"]).status_code == 200
    counted = [
        row
        for row in logged(account, workspace_token_id=issued["id"])
        if row["parent_call_id"] is None
    ]
    assert len(counted) == 200 + 9 + 50 + 1


def test_a_full_minute_waits_for_the_oldest_call(account):
    issued = account["issue"]()
    add_calls(account, issued["id"], 60)
    limited = call(account, issued["token"], "list_parameters")
    assert limited.status_code == 429
    assert 58 <= int(limited.headers["Retry-After"]) <= 60
    assert len(logged(account, workspace_token_id=issued["id"])) == 60


def test_token_values_never_reach_logs_error_bodies_or_the_call_log(account, capsys, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    with account["owner"].connect() as connection:
        before = connection.execute(select(func.coalesce(func.max(call_log.c.id), 0))).scalar_one()
    mcp, workspace = account["issue"](), account["issue"]("workspace")
    revoked = account["issue"]()
    assert account["browser"].delete(f"/api/me/tokens/{revoked['id']}").status_code == 204
    responses = [
        post(account, mcp["token"]),
        call(account, mcp["token"], "list_parameters"),
        call(account, mcp["token"], "list_missions", {"limit": "many"}),
        call(account, mcp["token"], "write_note", {"title": "x", "body": "y"}),
        call(account, mcp["token"], "no_such_tool"),
        post(account, mcp["token"], **{"X-Craft-User": "someone-else"}),
        post(account, workspace["token"]),
        post(account, revoked["token"]),
        account["python"].get("/api/me", headers={"Authorization": "Bearer " + mcp["token"]}),
        account["python"].get("/api/me", headers={"Authorization": "Bearer " + workspace["token"]}),
        account["python"].put(
            "/api/notes",
            json={"title": "x", "body": "y"},
            headers={"Authorization": "Bearer " + workspace["token"]},
        ),
        account["browser"].get("/api/me", headers={"Authorization": "Bearer " + mcp["token"]}),
    ]
    add_calls(account, mcp["id"], 60)
    responses.append(post(account, mcp["token"]))
    assert [r.status_code for r in responses] == [200] * 5 + [401] * 3 + [401, 200, 403, 401, 429]

    secrets = set()
    for issued in (mcp, workspace, revoked):
        raw = base64.urlsafe_b64decode(issued["token"] + "=" * (-len(issued["token"]) % 4))
        digest = hashlib.sha256(raw).digest()
        secrets |= {issued["token"], raw.hex(), digest.hex(), base64.b64encode(raw).decode()}
    captured = capsys.readouterr()
    server_logs = "\n".join(
        record.getMessage() + repr(record.__dict__)
        for record in caplog.records
        if not record.name.startswith(("httpx", "httpcore"))
    )
    bodies = "\n".join(r.text + repr(dict(r.headers)) for r in responses)
    # Every row written meanwhile, including refused sign-ins that name no account.
    with account["owner"].connect() as connection:
        rows = repr(connection.execute(select(call_log).where(call_log.c.id > before)).all())
    for where, text in [
        ("stdout and stderr", captured.out + captured.err),
        ("logs", server_logs),
        ("responses", bodies),
        ("call_log", rows),
    ]:
        assert not [secret for secret in secrets if secret in text], where
    # Issuing shows the token once, in its own response only.
    listed = account["browser"].get("/api/me/tokens").text
    assert not [secret for secret in secrets if secret in listed]


def test_a_short_token_list_leaves_run_token_calls_as_they_were(account):
    """The MCP SDK caches tool definitions for every caller; an MCP token's shorter list must
    not make a run token's next call list the tools again (one more logged resolution)."""
    mcp = account["issue"]()["token"]
    data = request(account)
    agent_service.start(account["caller"], data, "simulated")
    trusted = replace(account["caller"], conversation_id=data.thread_id, run_id=data.run_id)
    raw = issue_run_token(trusted, RunRef(run=data.run_id)).token

    def resolutions():
        return len(logged(account, name="agent.resolve_run_token", run_id=data.run_id))

    try:
        assert post(account, raw).status_code == 200
        assert post(account, mcp).status_code == 200
        start = resolutions()
        # write_note is not on the MCP token's list; the run token reaches it (approval-gated).
        refused = call(account, raw, "write_note", {"title": "x", "body": "y"}).json()["result"]
        assert refused["structuredContent"]["code"] == "approval_required"
        # Once per request: at sign-in, reused for its one message.
        assert resolutions() - start == 1
    finally:
        revoke_run_token(trusted, RunRef(run=data.run_id))
        agent_service.finish(trusted, data, "cancelled", {})


def test_a_run_token_resolves_once_per_request_and_reads_only_its_transport(account):
    """Issuing merges the transport ids into the stored state, and resolving reads them alone:
    neither reads nor rewrites the thread snapshot beside them."""
    import re

    from sqlalchemy import event
    from sqlalchemy.dialects.postgresql import insert

    sessions = m.metadata.tables["agent_sessions"]
    data = request(account)
    agent_service.start(account["caller"], data, "simulated")
    stored = {"craftAguiSnapshot": {"messages": ["generated"]}, "state": {"craftApprovals": []}}
    with account["owner"].begin() as connection:
        values = insert(sessions).values(conversation_id=data.thread_id, state=stored)
        connection.execute(
            values.on_conflict_do_update(
                index_elements=[sessions.c.conversation_id], set_={"state": stored}
            )
        )
    trusted = replace(account["caller"], conversation_id=data.thread_id, run_id=data.run_id)
    statements = []

    def record(connection, cursor, statement, *_):
        statements.append(statement)

    def resolutions():
        return len(logged(account, name="agent.resolve_run_token", run_id=data.run_id))

    engine = account["app"].state.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        raw = issue_run_token(trusted, RunRef(run=data.run_id)).token
        start = resolutions()
        assert post(account, raw).status_code == 200
        listed = call(account, raw, "list_missions").json()["result"]
        assert listed["isError"] is False
        assert resolutions() - start == 2
        with account["owner"].connect() as connection:
            state = connection.execute(
                select(sessions.c.state).where(sessions.c.conversation_id == data.thread_id)
            ).scalar_one()
    finally:
        event.remove(engine, "before_cursor_execute", record)
        revoke_run_token(trusted, RunRef(run=data.run_id))
        agent_service.finish(trusted, data, "cancelled", {})
    assert {key: state[key] for key in stored} == stored
    assert state["craftTransport"]["runId"] == str(data.run_id)
    touched = [s for s in statements if "agent_sessions.state" in s]
    assert touched
    assert not [s for s in touched if re.search(r"agent_sessions\.state(?!\s*(->|\|\|))", s)]
