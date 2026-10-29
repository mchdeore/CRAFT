"""The Hub client against a generated Hub API that checks CRAFT's one service token.

Covers start, stop and status, the agent's wait and kernel, and ``owner``: which person a Hub
token belongs to, including tokens that must name no one and a Hub that fails.
"""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.infrastructure import hub
from app.infrastructure.access import Caller
from app.infrastructure.config.settings import Settings
from app.workspace import service
from app.workspace.handlers import Workspace, read_workspace, start_workspace, stop_workspace

TOKEN = "generated-hub-token"
KERNEL = "00000000-0000-4000-8000-000000000201"


class GeneratedHub:
    """Just enough of JupyterHub's REST API and one person's Jupyter server."""

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.groups: dict[str, set[str]] = {}
        self.calls: list[tuple[str, str]] = []
        self.sessions: list[dict] = []
        self.files: set[str] = set()
        # token -> (Hub user name, scopes), as JupyterHub's GET /hub/api/user answers for it
        self.tokens: dict[str, tuple[str, list[str]]] = {}
        self.whoami_headers: list[str] = []
        self.whoami_status = 200

    def app(self) -> Starlette:
        async def guarded(request: Request):
            self.calls.append((request.method, request.url.path))
            if request.headers.get("authorization") != "token " + TOKEN:
                return Response(status_code=403)
            return None

        async def user(request: Request):
            if (refused := await guarded(request)) is not None:
                return refused
            name = request.path_params["name"]
            if request.method == "POST":
                if name in self.users:
                    return Response(status_code=409)
                self.users[name] = {"name": name, "servers": {}}
                return JSONResponse(self.users[name], status_code=201)
            if name not in self.users:
                return Response(status_code=404)
            found = self.users[name]
            server = found["servers"].get("")
            if server and server.get("pending") == "spawn":
                # The next read after a spawn sees the server ready.
                found["servers"][""] = {"ready": True, "pending": None}
                return JSONResponse({**found, "servers": {"": server}})
            return JSONResponse(found)

        async def server(request: Request):
            if (refused := await guarded(request)) is not None:
                return refused
            name = request.path_params["name"]
            if name not in self.users:
                return Response(status_code=404)
            servers = self.users[name]["servers"]
            if request.method == "POST":
                if "" in servers:
                    return Response(status_code=400)
                servers[""] = {"ready": False, "pending": "spawn"}
                return Response(status_code=202)
            if "" not in servers:
                return Response(status_code=400)
            del servers[""]
            return Response(status_code=204)

        async def group(request: Request):
            if (refused := await guarded(request)) is not None:
                return refused
            name = request.path_params["name"]
            if name in self.groups:
                return Response(status_code=409)
            self.groups[name] = set()
            return JSONResponse({"name": name}, status_code=201)

        async def members(request: Request):
            if (refused := await guarded(request)) is not None:
                return refused
            self.groups[request.path_params["name"]].update((await request.json())["users"])
            return JSONResponse({})

        async def sessions(request: Request):
            if (refused := await guarded(request)) is not None:
                return refused
            if request.method == "POST":
                body = await request.json()
                row = {"type": "notebook", "path": body["path"], "kernel": {"id": KERNEL}}
                self.sessions.append(row)
                return JSONResponse(row, status_code=201)
            return JSONResponse(self.sessions)

        async def contents(request: Request):
            if (refused := await guarded(request)) is not None:
                return refused
            path = request.path_params["path"]
            if request.method == "PUT":
                self.files.add(path)
                return JSONResponse({"path": path}, status_code=201)
            return JSONResponse({"path": path}) if path in self.files else Response(status_code=404)

        async def control(request: Request):
            if (refused := await guarded(request)) is not None:
                return refused
            if request.headers.get("host") != hub.CONTROL_HOST:
                return Response(status_code=421)
            return Response(status_code=405)

        async def whoami(request: Request):
            presented = request.headers.get("authorization", "")
            self.whoami_headers.append(presented)
            if self.whoami_status != 200:
                return Response(status_code=self.whoami_status)
            if presented == "token " + TOKEN:
                return JSONResponse({"kind": "service", "name": "craft"})
            name, scopes = self.tokens.get(presented.removeprefix("token "), (None, []))
            if name is None:
                return Response(status_code=403)
            return JSONResponse({"kind": "user", "name": name, "scopes": scopes})

        return Starlette(
            routes=[
                Route("/hub/api/user", whoami, methods=["GET"]),
                Route("/user/{name}/proxy/4040/mcp", control, methods=["GET"]),
                Route("/hub/api/users/{name}", user, methods=["GET", "POST"]),
                Route("/hub/api/users/{name}/server", server, methods=["POST", "DELETE"]),
                Route("/hub/api/groups/{name}", group, methods=["POST"]),
                Route("/hub/api/groups/{name}/users", members, methods=["POST"]),
                Route("/user/{name}/api/sessions", sessions, methods=["GET", "POST"]),
                Route("/user/{name}/api/contents/{path:path}", contents, methods=["GET", "PUT"]),
            ]
        )


@pytest.fixture
def generated(monkeypatch):
    found = GeneratedHub()
    transport = httpx.ASGITransport(app=found.app())
    original = hub._http

    def connect(selected, base):
        client = original(selected, base)
        client._transport = transport
        return client

    monkeypatch.setattr(hub, "_http", connect)
    return found


def caller(token: str = TOKEN) -> Caller:
    settings = Settings.from_env(
        {
            "CRAFT_APP_DB_PASSWORD": "generated-never-sent",
            "CRAFT_HUB_URL": "http://proxy-public",
            "CRAFT_HUB_PUBLIC_URL": "http://localhost:8200",
            "CRAFT_HUB_TOKEN": token,
        }
    )
    return Caller(user_id=uuid4(), settings=settings, origin="http")


def owner(person: Caller) -> UUID:
    assert person.user_id is not None
    return person.user_id


def test_open_starts_a_stopped_workspace_and_a_second_open_is_immediate(generated):
    person = caller()
    assert asyncio.run(read_workspace(person)) == Workspace(state="stopped", url=None)
    opened = asyncio.run(start_workspace(person))
    lab = f"http://localhost:8200/user/{person.user_id}/lab"
    # The generated Hub reports the spawn as ready on the next read.
    assert opened in {Workspace(state="starting", url=lab), Workspace(state="ready", url=lab)}
    assert asyncio.run(read_workspace(person)) == Workspace(state="ready", url=lab)
    spawned = [
        call for call in generated.calls if call[0] == "POST" and call[1].endswith("/server")
    ]
    assert asyncio.run(start_workspace(person)).state == "ready"
    again = [call for call in generated.calls if call[0] == "POST" and call[1].endswith("/server")]
    assert spawned == again  # a running server is never spawned twice
    assert set(generated.users) == {str(person.user_id)}


def test_stop_keeps_the_hub_user_and_reports_stopped(generated):
    person = caller()
    asyncio.run(start_workspace(person))
    assert asyncio.run(stop_workspace(person)) == Workspace(state="stopped", url=None)
    assert asyncio.run(stop_workspace(person)).state == "stopped"  # stopping twice is fine
    assert str(person.user_id) in generated.users


def test_first_start_links_the_craft_id_account_for_hub_sign_in(generated, monkeypatch):
    person = caller()
    tenant, account = str(uuid4()), str(uuid4())
    monkeypatch.setattr(service, "_identity", lambda _: (tenant, account))
    asyncio.run(start_workspace(person))
    asyncio.run(start_workspace(person))
    assert generated.groups == {f"idp-{tenant}-{account}": {str(person.user_id)}}


def test_a_wrong_service_token_is_refused_and_never_echoed(generated):
    from app.infrastructure.errors import ApiError

    with pytest.raises(ApiError) as refused:
        asyncio.run(start_workspace(caller("generated-wrong-token")))
    assert refused.value.code == "internal_error"
    assert "generated-wrong-token" not in str(refused.value)
    assert "generated-wrong-token" not in repr(hub.from_settings(caller().settings))


def test_the_agent_waits_for_ready_and_opens_one_analysis_kernel(generated):
    person = caller()
    selected = hub.from_settings(person.settings)
    assert selected is not None
    assert asyncio.run(selected.wait_ready(owner(person), seconds=5, every=0))
    assert asyncio.run(selected.kernel(owner(person))) == KERNEL
    assert asyncio.run(selected.kernel(owner(person))) == KERNEL
    assert len(generated.sessions) == 1 and generated.files == {hub.NOTEBOOK}


def test_waiting_is_bounded(generated, monkeypatch):
    async def starting(*_):
        return "starting"

    monkeypatch.setattr(hub.Hub, "state", starting)
    person = caller()
    selected = hub.from_settings(person.settings)
    assert selected is not None
    assert not asyncio.run(selected.wait_ready(owner(person), seconds=0.05, every=0.01))


def test_actions_refuse_a_workspace_that_is_not_ready(generated):
    from app.infrastructure.errors import Conflict

    with pytest.raises(Conflict):
        asyncio.run(service.run_code(caller(), "1"))
    assert not any(call[1].startswith("/user/") for call in generated.calls)


def test_without_a_hub_there_is_no_workspace():
    from app.infrastructure.errors import NotFound

    settings = Settings.from_env({"CRAFT_APP_DB_PASSWORD": "generated-never-sent"})
    with pytest.raises(NotFound):
        asyncio.run(read_workspace(Caller(user_id=uuid4(), settings=settings, origin="http")))


@pytest.mark.parametrize(
    "values",
    [
        {"CRAFT_HUB_URL": "http://proxy-public"},
        {"CRAFT_HUB_URL": "http://proxy-public/hub", "CRAFT_HUB_TOKEN": TOKEN},
        {"CRAFT_HUB_URL": "ftp://proxy-public", "CRAFT_HUB_TOKEN": TOKEN},
        {"CRAFT_HUB_URL": "http://hub", "CRAFT_HUB_PUBLIC_URL": "x", "CRAFT_HUB_TOKEN": TOKEN},
    ],
)
def test_hub_settings_are_strict_and_name_no_value(values):
    with pytest.raises(SystemExit) as stopped:
        Settings.from_env({"CRAFT_APP_DB_PASSWORD": "generated-never-sent", **values})
    assert TOKEN not in str(stopped.value) and "bad setting: hub_" in str(stopped.value)


def server_token(person: UUID) -> tuple[str, list[str]]:
    """What the Hub answers for a person's server token (JUPYTERHUB_API_TOKEN)."""
    return str(person), [f"access:servers!server={person}/", f"users:activity!user={person}"]


def test_a_server_token_names_its_user_and_is_sent_only_as_itself(generated):
    found: hub.JupyterHub | None = hub.from_settings(caller().settings)
    assert found is not None
    person = uuid4()
    generated.tokens["generated0server0token0one"] = server_token(person)
    assert asyncio.run(found.owner("generated0server0token0one")) == person
    # Asked with the presented token alone: CRAFT's service token never vouches for it.
    assert generated.whoami_headers == ["token generated0server0token0one"]


def test_tokens_that_must_name_no_one(generated):
    found = hub.from_settings(caller().settings)
    assert found is not None
    person = uuid4()
    generated.tokens.update(
        {
            # A name that isn't a CRAFT user ID.
            "generated0not0a0craft0user": ("someone", ["access:servers!server=someone/"]),
            # A token the person made for something else: it can't reach their server.
            "generated0narrow0token0000": (str(person), [f"read:users:name!user={person}"]),
            # Another person's server scope under this person's name.
            "generated0wrong0server0000": (str(person), [f"access:servers!server={uuid4()}/"]),
        }
    )
    for token in generated.tokens:
        assert asyncio.run(found.owner(token)) is None, token
    assert asyncio.run(found.owner(TOKEN)) is None  # CRAFT's own service token
    assert asyncio.run(found.owner("generated0unknown0token00")) is None


def test_a_failing_hub_raises_without_the_token(generated):
    found = hub.from_settings(caller().settings)
    assert found is not None
    generated.whoami_status = 502
    with pytest.raises(httpx.HTTPStatusError) as failed:
        asyncio.run(found.owner("generated0server0token0one"))
    assert "generated0server0token0one" not in str(failed.value)


def test_an_unreachable_hub_raises_without_the_token():
    unreachable = hub.Hub("http://127.0.0.1:1", "http://127.0.0.1:1", TOKEN)
    with pytest.raises(httpx.TransportError) as failed:
        asyncio.run(unreachable.owner("generated0server0token0one"))
    assert "generated0server0token0one" not in str(failed.value) + repr(unreachable)
