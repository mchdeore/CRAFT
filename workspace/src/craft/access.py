"""CRAFT access points: the same web routes, sign-in rules and logging a person uses.

Each function sends one HTTP request to a CRAFT route with this account's
workspace token and returns a ``Result``. The server resolves the token to the
account, applies its mission and silo grants, allows only read routes and logs
every call with the ``workspace`` origin.

The token is, in order: the one ``login()`` set for this kernel, the file at
``CRAFT_TOKEN_FILE``, or, in a workspace JupyterHub started, one CRAFT issues for
this server's own Hub token (``JUPYTERHUB_API_TOKEN``) through
``POST /api/workspace-tokens/hub``. A swapped token is kept in kernel memory only.
"""

import getpass
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

_CAMEL = {"known_at": "knownAt", "valid_from": "validFrom"}
_TABLE_KEYS = (
    "hits",
    "documents",
    "chunks",
    "missions",
    "values",
    "parameters",
    "candidates",
    "blocks",
    "facets",
    "skills",
    "tools",
    "outline",
)


class CraftError(RuntimeError):
    """A CRAFT tool returned an error result."""


@dataclass(frozen=True)
class Result:
    """One access point result: the structured data, its citations and a table view."""

    tool: str
    data: dict[str, Any] = field(repr=False)

    @property
    def citations(self) -> list[dict[str, Any]]:
        """Every citation handle in the result, in order and without repeats."""
        found: list[dict[str, Any]] = []
        seen: set[tuple] = set()

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                if {"item", "version", "block", "quoteSha256"} <= value.keys():
                    key = (value["item"], value["version"], value["block"], value["quoteSha256"])
                    if key not in seen:
                        seen.add(key)
                        found.append(value)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(self.data)
        return found

    @property
    def df(self):
        """A pandas table of the result's main rows; nested fields become dotted columns."""
        import pandas as pd

        if self.tool == "compare_missions":
            rows = []
            for row in self.data.get("rows", []):
                record = {"mission": row["mission"]["key"]}
                for parameter, value in zip(self.data["parameters"], row["values"], strict=True):
                    record[parameter] = None if value is None else value.get("low")
                    record[parameter + "_unit"] = None if value is None else value.get("unit")
                rows.append(record)
            return pd.DataFrame(rows)
        if self.tool == "find_missions":
            rows = [
                {"mission": entry["mission"]["key"], **value}
                for entry in self.data.get("missions", [])
                for value in entry.get("matched", [])
            ]
            return pd.json_normalize(rows)
        for key in _TABLE_KEYS:
            rows = self.data.get(key)
            if isinstance(rows, list):
                return pd.json_normalize(rows)
        return pd.json_normalize(self.data)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def _repr_html_(self) -> str:
        import html

        table = self.df.head(20).to_html(index=False)
        heading = html.escape(f"{self.tool}: {len(self.citations)} citations")
        return f"<p><strong>{heading}</strong></p>{table}"


def _snake_to_api(values: dict[str, Any]) -> dict[str, Any]:
    return {_CAMEL.get(key, key): value for key, value in values.items() if value is not None}


TOKEN_FILE = Path(os.environ.get("CRAFT_TOKEN_FILE", "/run/craft/workspace-token"))
_session_token: str | None = None
_hub_token: str | None = None  # swapped for this server's own Hub token, once per kernel
_client = None


def login(token: str | None = None) -> None:
    """Use a workspace token for this kernel only; it is never written to a file."""
    global _session_token, _client
    value = token if token is not None else getpass.getpass("CRAFT workspace token: ")
    _session_token = value.strip() or None
    if _client is not None:
        _client.close()
        _client = None


def _api_url() -> str:
    url = os.environ.get("CRAFT_API_URL", "").rstrip("/")
    target = urlsplit(url)
    if (
        target.scheme not in {"http", "https"}
        or not target.hostname
        or target.username is not None
        or target.password is not None
        or target.path not in {"", "/"}
        or target.query
        or target.fragment
    ):
        raise CraftError("CRAFT_API_URL must be the plain origin of the CRAFT server")
    return url


def _token() -> str:
    if _session_token:
        return _session_token
    try:
        value = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value
    if os.environ.get("JUPYTERHUB_API_TOKEN"):
        return _from_hub()
    raise CraftError(
        "No CRAFT workspace token. Create one in CRAFT and run craft.login() in this kernel."
    )


def _from_hub() -> str:
    """A workspace token for the person the Hub started this server for; kept in memory only."""
    global _hub_token
    if _hub_token is not None:
        return _hub_token
    import httpx

    swapped = httpx.post(
        _api_url() + "/api/workspace-tokens/hub",
        headers={"Authorization": "token " + os.environ["JUPYTERHUB_API_TOKEN"]},
        trust_env=False,
        follow_redirects=False,
        timeout=30,
    )
    if swapped.status_code != 200:
        raise CraftError(
            f"CRAFT gave this workspace no token (HTTP {swapped.status_code}). "
            "Create one in CRAFT and run craft.login() in this kernel."
        )
    token = str(swapped.json()["token"])
    _hub_token = token
    return token


def _renew() -> bool:
    """Forget a swapped token CRAFT refused (expired or revoked) so the next request swaps again.

    False when no swapped token was in use. The server's Hub token stays the credential: while
    the server runs and the account is enabled, a new swap succeeds.
    """
    global _hub_token, _client
    if _session_token or _hub_token is None:
        return False
    _hub_token = None
    if _client is not None:
        _client.close()
        _client = None
    return True


def _http():
    global _client
    if _client is None:
        from http.cookiejar import CookieJar, DefaultCookiePolicy

        import httpx

        _client = httpx.Client(
            base_url=_api_url() + "/api",
            headers={"Authorization": "Bearer " + _token()},
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
            trust_env=False,
            follow_redirects=False,
            timeout=120,
        )
    return _client


def _request(tool: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Result:
    values = {key: value for key, value in (payload or {}).items() if value is not None}

    def send():
        if method == "GET":
            return _http().get(path, params=values)
        return _http().request(method, path, json=values)

    response = send()
    if response.status_code == 401 and _renew():
        response = send()  # a refused swapped token is swapped again, once
    try:
        data = response.json() if response.content else {}
    except ValueError:
        data = {}
    if response.status_code >= 400:
        if isinstance(data, dict) and "code" in data:
            raise CraftError(
                f"{data['code']}: {data.get('message', '')} (request {data.get('requestId', '')})"
            )
        raise CraftError(f"{tool} failed with HTTP {response.status_code}")
    return Result(tool, data if isinstance(data, dict) else {"items": data})


def _part(value: str) -> str:
    return quote(str(value), safe="")


def whoami() -> Result:
    """The account this workspace token signs in as."""
    return _request("me", "GET", "/me")


# Documents


def search(
    query: str,
    *,
    missions: list[str] | None = None,
    silos: list[str] | None = None,
    stages: list[str] | None = None,
    kinds: list[str] | None = None,
    limit: int | None = None,
) -> Result:
    """Search readable documents; each hit carries a citation handle."""
    filters = _snake_to_api(
        {"missions": missions, "silos": silos, "stages": stages, "kinds": kinds, "limit": limit}
    )
    return _request("search", "POST", "/library/search", {"query": query, **filters})


def documents(limit: int | None = None) -> Result:
    """List readable documents with their current versions."""
    return _request("list_documents", "GET", "/library/documents", {"limit": limit})


def document(item: str, version: int | None = None) -> Result:
    """A document version's tags, summary, outline and version list."""
    return _request(
        "get_document", "GET", f"/library/documents/{_part(item)}", {"version": version}
    )


def chunks(item: str, version: int, from_block: int = 1, to_block: int | None = None) -> Result:
    """Up to 20 consecutive blocks of one document version."""
    last = from_block + 19 if to_block is None else to_block
    return _request(
        "read_chunks",
        "GET",
        f"/library/documents/{_part(item)}/chunks",
        {"version": version, "fromBlock": from_block, "toBlock": last},
    )


def explore(
    handle: dict[str, Any],
    *,
    before: int | None = None,
    after: int | None = None,
    query: str | None = None,
) -> Result:
    """The blocks around a citation handle, optionally scored against a query."""
    return _request(
        "explore",
        "POST",
        "/library/explore",
        {"handle": handle, "before": before, "after": after, "query": query},
    )


def check_citation(handle: dict[str, Any]) -> Result:
    """Whether a quote is still current, changed, newer or withdrawn."""
    return _request("check_citation", "POST", "/library/citations/check", {"handle": handle})


def compare_versions(item: str, from_version: int, to_version: int) -> Result:
    """Blocks added, removed, changed or moved between two versions."""
    return _request(
        "compare_versions",
        "GET",
        f"/library/documents/{_part(item)}/compare",
        {"fromVersion": from_version, "toVersion": to_version},
    )


def capabilities() -> Result:
    """The tools this run can use."""
    return _request("list_capabilities", "GET", "/tools/capabilities")


class _Cube:
    """Engineering datacube access points: missions, parameters and cited values."""

    @staticmethod
    def parameters() -> Result:
        """The parameter dictionary with units and conversions."""
        return _request("list_parameters", "GET", "/cube/parameters")

    @staticmethod
    def missions(query: str | None = None, limit: int | None = None) -> Result:
        """Readable missions, resolving names and aliases."""
        return _request("list_missions", "GET", "/cube/missions", {"query": query, "limit": limit})

    @staticmethod
    def find(
        *conditions: tuple | dict,
        silo: str | None = None,
        stage: str | None = None,
        at: str | None = None,
        known_at: str | None = None,
        limit: int | None = None,
    ) -> Result:
        """Missions matching every condition.

        A condition is ``(parameter, op, value, unit)``, ``(parameter, "between",
        low, high, unit)`` or the API's dictionary form. Operators: eq, ge, le, between.
        Example: ``cube.find(("mass_dry", "le", 150, "kg"), stage="current")``.
        """
        shaped = [_condition(value) for value in conditions]
        scope = _snake_to_api(
            {"silo": silo, "stage": stage, "at": at, "known_at": known_at, "limit": limit}
        )
        return _request("find_missions", "POST", "/cube/find", {"conditions": shaped, **scope})

    @staticmethod
    def mission(
        key: str,
        *,
        silo: str | None = None,
        stage: str | None = None,
        at: str | None = None,
        known_at: str | None = None,
    ) -> Result:
        """A mission's values on a date, with gaps listed."""
        scope = _snake_to_api({"silo": silo, "stage": stage, "at": at, "known_at": known_at})
        return _request("get_mission", "GET", f"/cube/missions/{_part(key)}", scope)

    @staticmethod
    def compare(
        keys: list[str],
        parameters: list[str],
        *,
        silo: str | None = None,
        stage: str | None = None,
        at: str | None = None,
        known_at: str | None = None,
    ) -> Result:
        """Two to five missions side by side; ``.df`` is one row per mission."""
        scope = _snake_to_api({"silo": silo, "stage": stage, "at": at, "known_at": known_at})
        return _request(
            "compare_missions",
            "POST",
            "/cube/compare",
            {"keys": keys, "parameters": parameters, **scope},
        )

    @staticmethod
    def evidence(value_id: str) -> Result:
        """A value's quote, source number, unit, conversion and citation status."""
        return _request("get_value_evidence", "GET", f"/cube/values/{_part(value_id)}")

    @staticmethod
    def similar(key: str, facets: list[str] | None = None, limit: int | None = None) -> Result:
        """Missions ranked by facet similarity."""
        return _request(
            "similar_missions",
            "POST",
            "/cube/similar",
            {"key": key, "facets": facets, "limit": limit},
        )

    @staticmethod
    def explain(key: str, other: str, facets: list[str] | None = None) -> Result:
        """Per-facet scores and texts behind a similarity."""
        return _request(
            "explain_similarity",
            "POST",
            "/cube/similar/explain",
            {"key": key, "other": other, "facets": facets},
        )

    @staticmethod
    def candidates(
        handle: dict[str, Any],
        *,
        mission: str | None = None,
        silo: str | None = None,
        stage: str | None = None,
        valid_from: str | None = None,
    ) -> Result:
        """Candidate values read from one table chunk. Nothing is written."""
        context = _snake_to_api(
            {"mission": mission, "silo": silo, "stage": stage, "valid_from": valid_from}
        )
        return _request("prepare_values", "POST", "/cube/candidates", {"handle": handle, **context})


cube = _Cube()


def _condition(value: tuple | dict) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if len(value) == 4:
        parameter, op, number, unit = value
        return {"parameter": parameter, "op": op, "value": str(number), "unit": unit}
    if len(value) == 5 and value[1] == "between":
        parameter, op, low, high, unit = value
        return {
            "parameter": parameter,
            "op": op,
            "value": str(low),
            "high": str(high),
            "unit": unit,
        }
    raise ValueError(
        "A condition is (parameter, op, value, unit) or (parameter, 'between', low, high, unit)"
    )
