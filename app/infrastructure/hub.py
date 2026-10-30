"""JupyterHub, reached with CRAFT's one service token (deploy/jupyterhub/values.yaml).

``JupyterHub`` is what the rest of CRAFT needs from the Hub; ``Hub`` is its one implementation,
and tests may stand in their own. A person's Hub name is their CRAFT user ID, so no mapping is
kept here. The one call made with another token is ``owner``, which asks the Hub about the
token it is given, as that token. Tokens are never logged: the service token lives in a
repr-less field, and every token travels only in an ``Authorization`` header.

A failure of the Hub or of a server behind it is an ``ApiError`` (``checked`` and ``client``), so
a route answers 404, 409 or 503 instead of 500.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field, replace
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Literal, Protocol
from urllib.parse import quote
from uuid import UUID

import httpx

from app.infrastructure.errors import Conflict, InvalidInput, NotFound, WorkspaceUnavailable

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
        """Whether the person's server is stopped, starting or ready; a pending stop is starting."""
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
        """Start if needed (again after a pending stop) and wait, bounded, for ``ready`` and the
        agent's read tools."""
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
        server = (checked(found).json().get("servers") or {}).get("")
        if not server:
            return "stopped"
        if server.get("pending") == "stop":
            return "starting"  # the Hub refuses a start until the stop ends
        return "ready" if server.get("ready") else "starting"

    async def start(self, user_id: UUID) -> None:
        async with _api(self) as http:
            started = await http.post(_user(user_id) + "/server")
            if started.status_code == 404:
                # First start before the person ever signed in to the Hub.
                checked(await http.post(_user(user_id)))
                started = await http.post(_user(user_id) + "/server")
            if started.status_code == 400 and await self.state(user_id) != "stopped":
                return  # already running or starting, or a stop is still pending
            checked(started)

    async def stop(self, user_id: UUID) -> None:
        async with _api(self) as http:
            stopped = await http.delete(_user(user_id) + "/server")
        if stopped.status_code in {400, 404}:
            return  # not running, or no Hub user yet
        checked(stopped)

    async def link(self, user_id: UUID, tenant: str, account: str) -> None:
        # The Hub user joins their craft-id account's sign-in group (craft-names in the values).
        group = "groups/" + quote(f"idp-{tenant}-{account}")
        async with _api(self) as http:
            made = await http.post(group)
            if made.status_code != 409:  # 409: it exists already
                checked(made)
            checked(await http.post(group + "/users", json={"users": [str(user_id)]}))

    async def owner(self, token: str) -> UUID | None:
        # Asked with the presented token itself, never with CRAFT's service token.
        async with _api(replace(self, token=token)) as http:
            found = await http.get("user")
        if found.status_code in {401, 403}:
            return None
        body = checked(found).json()
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
                # Stopped is started, also once a stop that was still pending has ended.
                while (state := await self.state(user_id)) != "ready":
                    if state == "stopped":
                        await self.start(user_id)
                    await asyncio.sleep(every)
                while not await self._control_up(user_id):
                    await asyncio.sleep(every)
        except TimeoutError:
            return False
        return True

    async def kernel(self, user_id: UUID) -> str:
        async with client(self.server(user_id), self.token) as http:
            sessions = await http.get("api/sessions")
            checked(sessions)
            for row in sessions.json():
                if row.get("type") == "notebook" and row.get("path") == NOTEBOOK:
                    return row["kernel"]["id"]
            found = await http.get("api/contents/" + NOTEBOOK, params={"content": "0"})
            if found.status_code == 404:
                empty = {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
                made = await http.put(
                    "api/contents/" + NOTEBOOK, json={"type": "notebook", "content": empty}
                )
                checked(made)
            else:
                checked(found)
            opened = await http.post(
                "api/sessions",
                json={"path": NOTEBOOK, "type": "notebook", "kernel": {"name": "python3"}},
            )
            checked(opened)
            return opened.json()["kernel"]["id"]

    async def _control_up(self, user_id: UUID) -> bool:
        """The control server answers (any status below 500); it starts a little after Lab."""
        async with client(self.server(user_id), self.token) as http:
            # Without text/event-stream it answers 406 at once instead of opening an event stream.
            found = await http.get(
                CONTROL, headers={**self.control_headers(), "Accept": "application/json"}
            )
        return found.status_code < 500


def checked(response: httpx.Response) -> httpx.Response:
    """The response if it succeeded, else what its failure means to CRAFT's caller.

    404 is ``NotFound``, 409 ``Conflict`` and 400 or 422 ``InvalidInput``. Anything else, such as
    a 5xx or the 401 or 403 of a wrong service token, is ``WorkspaceUnavailable``.
    """
    if response.is_success:
        return response
    if response.status_code == 404:
        raise NotFound()
    if response.status_code == 409:
        raise Conflict()
    if response.status_code in {400, 422}:
        raise InvalidInput()
    raise WorkspaceUnavailable()


@asynccontextmanager
async def client(base: str, token: str, timeout: float = 30) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP to the Hub or a server behind it; one that can't be reached is unavailable."""
    try:
        async with httpx.AsyncClient(
            base_url=base,
            headers={"Authorization": "token " + token},
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
            trust_env=False,
            follow_redirects=False,
            timeout=timeout,
        ) as http:
            yield http
    except httpx.TransportError:
        raise WorkspaceUnavailable() from None


def _api(hub: Hub) -> AbstractAsyncContextManager[httpx.AsyncClient]:
    return client(hub.url.rstrip("/") + "/hub/api/", hub.token)


def _user(user_id: UUID) -> str:
    return "users/" + quote(str(user_id))
