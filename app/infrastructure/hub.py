"""JupyterHub, reached only with CRAFT's one service token (deploy/jupyterhub/values.yaml).

A person's Hub name is their CRAFT user ID, so no mapping is kept here. The token is never
logged: it lives in a repr-less field and only in request headers.
"""

import asyncio
from dataclasses import dataclass, field
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Literal
from urllib.parse import quote
from uuid import UUID

import httpx

State = Literal["stopped", "starting", "ready"]
NOTEBOOK = "analysis.ipynb"  # the notebook whose kernel the agent's runs work in
CONTROL = "proxy/4040/mcp"  # the notebook control server beside JupyterLab in the person's pod
# It refuses any Host but its own loopback one (DNS-rebinding protection), and JupyterLab's proxy
# forwards the Host it receives.
CONTROL_HOST = "127.0.0.1:4040"
READ_TOOLS = ("list_files", "list_notebooks", "read_notebook")


@dataclass(frozen=True)
class Hub:
    url: str
    public_url: str
    token: str = field(repr=False)

    def server(self, user_id: UUID) -> str:
        """The person's own server, reached through the Hub proxy."""
        return f"{self.url.rstrip('/')}/user/{quote(str(user_id))}/"

    def lab(self, user_id: UUID) -> str:
        """JupyterLab as the browser opens it; the Hub signs the person in with craft-id."""
        return f"{self.public_url.rstrip('/')}/user/{quote(str(user_id))}/lab"

    def headers(self) -> dict[str, str]:
        return {"Authorization": "token " + self.token}

    def control_headers(self) -> dict[str, str]:
        return {**self.headers(), "Host": CONTROL_HOST}


def from_settings(settings) -> Hub | None:
    if settings is None or not settings.hub_url:
        return None
    return Hub(settings.hub_url, settings.hub_public_url or settings.hub_url, settings.hub_token)


def _http(hub: Hub, base: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base,
        headers=hub.headers(),
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        trust_env=False,
        follow_redirects=False,
        timeout=30,
    )


def _api(hub: Hub) -> httpx.AsyncClient:
    return _http(hub, hub.url.rstrip("/") + "/hub/api/")


def _user(user_id: UUID) -> str:
    return "users/" + quote(str(user_id))


async def state(hub: Hub, user_id: UUID) -> State:
    async with _api(hub) as http:
        found = await http.get(_user(user_id))
    if found.status_code == 404:
        return "stopped"
    found.raise_for_status()
    server = (found.json().get("servers") or {}).get("")
    if not server or server.get("pending") == "stop":
        return "stopped"
    return "ready" if server.get("ready") else "starting"


async def start(hub: Hub, user_id: UUID) -> None:
    """Ask the Hub to spawn the server and return at once; the Hub tracks its progress."""
    async with _api(hub) as http:
        started = await http.post(_user(user_id) + "/server")
        if started.status_code == 404:
            # First start before the person ever signed in to the Hub.
            (await http.post(_user(user_id))).raise_for_status()
            started = await http.post(_user(user_id) + "/server")
        if started.status_code == 400 and await state(hub, user_id) != "stopped":
            return  # already running or starting
        started.raise_for_status()


async def link(hub: Hub, user_id: UUID, tenant: str, account: str) -> None:
    """Put the Hub user in their craft-id account's sign-in group (craft-names in the values)."""
    group = "groups/" + quote(f"idp-{tenant}-{account}")
    async with _api(hub) as http:
        made = await http.post(group)
        if made.status_code != 409:  # 409: it exists already
            made.raise_for_status()
        added = await http.post(group + "/users", json={"users": [str(user_id)]})
        added.raise_for_status()


async def stop(hub: Hub, user_id: UUID) -> None:
    async with _api(hub) as http:
        stopped = await http.delete(_user(user_id) + "/server")
    if stopped.status_code in {400, 404}:
        return  # not running, or no Hub user yet
    stopped.raise_for_status()


async def _control_up(hub: Hub, user_id: UUID) -> bool:
    """The control server answers (any status below 500); it starts a little after JupyterLab."""
    async with _http(hub, hub.server(user_id)) as http:
        # Without text/event-stream it answers 406 at once instead of opening an event stream.
        found = await http.get(
            CONTROL, headers={**hub.control_headers(), "Accept": "application/json"}
        )
    return found.status_code < 500


async def wait_ready(hub: Hub, user_id: UUID, seconds: float = 90, every: float = 1) -> bool:
    """Start if needed and wait, bounded, for ``ready`` and the agent's read tools."""
    try:
        async with asyncio.timeout(seconds):
            if await state(hub, user_id) == "stopped":
                await start(hub, user_id)
            while await state(hub, user_id) != "ready":
                await asyncio.sleep(every)
            while not await _control_up(hub, user_id):
                await asyncio.sleep(every)
    except TimeoutError:
        return False
    return True


async def kernel(hub: Hub, user_id: UUID) -> str:
    """The kernel of the person's analysis notebook, opened when it has none."""
    async with _http(hub, hub.server(user_id)) as http:
        sessions = await http.get("api/sessions")
        sessions.raise_for_status()
        for row in sessions.json():
            if row.get("type") == "notebook" and row.get("path") == NOTEBOOK:
                return row["kernel"]["id"]
        found = await http.get("api/contents/" + NOTEBOOK, params={"content": "0"})
        if found.status_code == 404:
            empty = {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
            made = await http.put(
                "api/contents/" + NOTEBOOK, json={"type": "notebook", "content": empty}
            )
            made.raise_for_status()
        else:
            found.raise_for_status()
        opened = await http.post(
            "api/sessions",
            json={"path": NOTEBOOK, "type": "notebook", "kernel": {"name": "python3"}},
        )
        opened.raise_for_status()
        return opened.json()["kernel"]["id"]
