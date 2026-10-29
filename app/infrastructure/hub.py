"""JupyterHub, reached only with CRAFT's one service token (deploy/jupyterhub/values.yaml).

``JupyterHub`` is what the rest of CRAFT needs from the Hub; ``Hub`` is its one implementation,
and tests may stand in their own. A person's Hub name is their CRAFT user ID, so no mapping is
kept here. Tokens are never logged: the service token lives in a repr-less field, and every
token travels only in an ``Authorization`` header.
"""

import asyncio
import re
from dataclasses import dataclass, field, replace
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Literal, Protocol
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
# The shape of a Hub API token, such as a person's server holds (JUPYTERHUB_API_TOKEN). Anything
# else is refused before it is sent anywhere.
TOKEN = re.compile(r"[A-Za-z0-9_.-]{16,256}")


class JupyterHub(Protocol):
    """Everything CRAFT asks of JupyterHub. Every ``user_id`` is a CRAFT user ID (the Hub name)."""

    @property
    def token(self) -> str:
        """CRAFT's service token, for calls through the Hub proxy to a person's server."""
        ...

    def server(self, user_id: UUID) -> str:
        """The person's own server, reached through the Hub proxy."""
        ...

    def lab(self, user_id: UUID) -> str:
        """JupyterLab as the browser opens it; the Hub signs the person in with craft-id."""
        ...

    def control_headers(self) -> dict[str, str]:
        """Headers for the notebook control server beside the person's JupyterLab."""
        ...

    async def state(self, user_id: UUID) -> State:
        """Whether the person's server is stopped, starting or ready."""
        ...

    async def start(self, user_id: UUID) -> None:
        """Ask the Hub to start the server and return at once, creating the Hub user if needed."""
        ...

    async def stop(self, user_id: UUID) -> None:
        """Stop the server; its volume is kept. Already stopped is fine."""
        ...

    async def link(self, user_id: UUID, tenant: str, account: str) -> None:
        """Let the person sign in to the Hub with their craft-id account."""
        ...

    async def owner(self, token: str) -> UUID | None:
        """The CRAFT user whose server holds ``token``, as the Hub answers; else None.

        Only a token that can reach its owner's own server counts, as the server's
        ``JUPYTERHUB_API_TOKEN`` can: a narrower token the person made for something else, or a
        service's token, names no one here.
        """
        ...

    async def wait_ready(self, user_id: UUID, seconds: float = 90, every: float = 1) -> bool:
        """Start if needed and wait, bounded, for ``ready`` and the agent's read tools."""
        ...

    async def kernel(self, user_id: UUID) -> str:
        """The kernel of the person's analysis notebook, opened when it has none."""
        ...


def from_settings(settings) -> JupyterHub | None:
    """The configured Hub (``CRAFT_HUB_*``), or None when this deployment has none."""
    if settings is None or not settings.hub_url:
        return None
    return Hub(settings.hub_url, settings.hub_public_url or settings.hub_url, settings.hub_token)


@dataclass(frozen=True)
class Hub:
    """The Hub at ``url`` (inside the cluster) and ``public_url`` (as the browser sees it)."""

    url: str
    public_url: str
    token: str = field(repr=False)

    def server(self, user_id: UUID) -> str:
        return f"{self.url.rstrip('/')}/user/{quote(str(user_id))}/"

    def lab(self, user_id: UUID) -> str:
        return f"{self.public_url.rstrip('/')}/user/{quote(str(user_id))}/lab"

    def headers(self) -> dict[str, str]:
        return {"Authorization": "token " + self.token}

    def control_headers(self) -> dict[str, str]:
        return {**self.headers(), "Host": CONTROL_HOST}

    async def state(self, user_id: UUID) -> State:
        async with _api(self) as http:
            found = await http.get(_user(user_id))
        if found.status_code == 404:
            return "stopped"
        found.raise_for_status()
        server = (found.json().get("servers") or {}).get("")
        if not server or server.get("pending") == "stop":
            return "stopped"
        return "ready" if server.get("ready") else "starting"

    async def start(self, user_id: UUID) -> None:
        async with _api(self) as http:
            started = await http.post(_user(user_id) + "/server")
            if started.status_code == 404:
                # First start before the person ever signed in to the Hub.
                (await http.post(_user(user_id))).raise_for_status()
                started = await http.post(_user(user_id) + "/server")
            if started.status_code == 400 and await self.state(user_id) != "stopped":
                return  # already running or starting
            started.raise_for_status()

    async def stop(self, user_id: UUID) -> None:
        async with _api(self) as http:
            stopped = await http.delete(_user(user_id) + "/server")
        if stopped.status_code in {400, 404}:
            return  # not running, or no Hub user yet
        stopped.raise_for_status()

    async def link(self, user_id: UUID, tenant: str, account: str) -> None:
        # The Hub user joins their craft-id account's sign-in group (craft-names in the values).
        group = "groups/" + quote(f"idp-{tenant}-{account}")
        async with _api(self) as http:
            made = await http.post(group)
            if made.status_code != 409:  # 409: it exists already
                made.raise_for_status()
            added = await http.post(group + "/users", json={"users": [str(user_id)]})
            added.raise_for_status()

    async def owner(self, token: str) -> UUID | None:
        # Asked with the presented token itself, never with CRAFT's service token.
        async with _api(replace(self, token=token)) as http:
            found = await http.get("user")
        if found.status_code in {401, 403}:
            return None
        found.raise_for_status()
        body = found.json()
        if not isinstance(body, dict) or body.get("kind") != "user":
            return None  # a service's token names no person
        try:
            user_id = UUID(str(body.get("name")))
        except ValueError:
            return None
        if f"access:servers!server={user_id}/" not in (body.get("scopes") or []):
            return None
        return user_id

    async def wait_ready(self, user_id: UUID, seconds: float = 90, every: float = 1) -> bool:
        try:
            async with asyncio.timeout(seconds):
                if await self.state(user_id) == "stopped":
                    await self.start(user_id)
                while await self.state(user_id) != "ready":
                    await asyncio.sleep(every)
                while not await self._control_up(user_id):
                    await asyncio.sleep(every)
        except TimeoutError:
            return False
        return True

    async def kernel(self, user_id: UUID) -> str:
        async with _http(self, self.server(user_id)) as http:
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

    async def _control_up(self, user_id: UUID) -> bool:
        """The control server answers (any status below 500); it starts a little after Lab."""
        async with _http(self, self.server(user_id)) as http:
            # Without text/event-stream it answers 406 at once instead of opening an event stream.
            found = await http.get(
                CONTROL, headers={**self.control_headers(), "Accept": "application/json"}
            )
        return found.status_code < 500


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
