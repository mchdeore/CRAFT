"""Start, stop and status through a generated Hub API that checks CRAFT's one service token."""

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

        return Starlette(
            routes=[
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
    assert asyncio.run(hub.wait_ready(selected, owner(person), seconds=5, every=0))
    assert asyncio.run(hub.kernel(selected, owner(person))) == KERNEL
    assert asyncio.run(hub.kernel(selected, owner(person))) == KERNEL
    assert len(generated.sessions) == 1 and generated.files == {hub.NOTEBOOK}


def test_waiting_is_bounded(generated, monkeypatch):
    async def starting(*_):
        return "starting"

    monkeypatch.setattr(hub, "state", starting)
    person = caller()
    selected = hub.from_settings(person.settings)
    assert selected is not None
    assert not asyncio.run(hub.wait_ready(selected, owner(person), seconds=0.05, every=0.01))


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
