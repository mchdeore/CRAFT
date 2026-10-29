"""Typed, validated settings. A bad value stops startup and names the field, never the value."""

import re
from collections.abc import Mapping
from typing import Annotated, Literal

import msgspec

from app.infrastructure.config.env import read_env

PREFIX = "CRAFT_"
# Setup and Compose only; scripts/seed reads CRAFT_SEED.
OTHER_TOOLS = {"db_admin_password", "owner_db_password", "mcp_account", "seed"}
IDENTITY = {"id_db_password", "id_signing_key", "id_public_url"}  # craft-id's own settings
# Settings of the deleted indexer, RQ queue and dev page; older .env files may still set them.
RETIRED = {
    "model_ocr",
    "ocr_max_bytes",
    "index_max_bytes",
    "ocr_attempt_limit",
    "ocr_retry_seconds",
    "queue_url",
    "queue_name",
    "dev_page",
    # Replaced by per-queue CRAFT_WORKER_CONCURRENCY_AGENT / _INTERACTIVE / _BULK.
    "worker_concurrency",
}
QUEUES = ("agent", "interactive", "bulk")
# High default until measured; a worker always needs some concurrency number.
DEFAULT_WORKER_CONCURRENCY = 50
MAX_WORKER_CONCURRENCY = 1000


class Settings(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "craft_dev"
    provider_root: str = ".local/runtime/provider"
    # Where originals are read: the simulated folder, or a LiveLink-shaped API in front of it.
    provider: Literal["simulated", "livelink_test"] = "simulated"
    provider_url: str = ""
    provider_token: str = ""
    process_root: str = ""
    worker_queues: str = "agent,interactive,bulk"
    worker_concurrency_agent: Annotated[int, msgspec.Meta(ge=1, le=MAX_WORKER_CONCURRENCY)] = (
        DEFAULT_WORKER_CONCURRENCY
    )
    worker_concurrency_interactive: Annotated[
        int, msgspec.Meta(ge=1, le=MAX_WORKER_CONCURRENCY)
    ] = DEFAULT_WORKER_CONCURRENCY
    worker_concurrency_bulk: Annotated[int, msgspec.Meta(ge=1, le=MAX_WORKER_CONCURRENCY)] = (
        DEFAULT_WORKER_CONCURRENCY
    )
    session_idle_hours: Annotated[int, msgspec.Meta(gt=0)] = 8
    session_absolute_hours: Annotated[int, msgspec.Meta(gt=0)] = 12
    sign_in_attempts_per_minute: Annotated[int, msgspec.Meta(gt=0)] = 5
    password_min_length: Annotated[int, msgspec.Meta(ge=1)] = 12
    allowed_origins: str = "http://localhost:8000"
    cross_origin_browser: bool = False
    model_main: str = "gpt-5.2"
    model_check: str = "gpt-5.2"
    model_embedding: str = "text-embedding-3-large"
    model_endpoint: str = ""
    model_key: str = ""
    model_max_output_tokens: Annotated[int, msgspec.Meta(ge=16, le=16000)] = 4000
    agent_run_seconds: Annotated[int, msgspec.Meta(ge=30, le=900)] = 120
    wolfram_app_id: str = ""
    mcp_url: str = "http://127.0.0.1:8000/mcp"
    # Whole-notebook runs start this workspace image in a sandbox with no network.
    notebook_image: str = "craft-user-workspace:local"
    # JupyterHub (deploy/jupyterhub): its address inside the cluster, the address the browser
    # opens (empty: the same) and CRAFT's one service token. Empty URL: no workspaces.
    hub_url: str = ""
    hub_public_url: str = ""
    hub_token: str = ""
    # Sign-in: craft-id now, Entra later. Only these three settings change at the switch.
    oidc_authority: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:8000/api/auth/callback"
    # Username and password straight to craft-app (POST /api/session): tests only.
    local_password_signin: bool = False
    app_db_password: str
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Build from CRAFT_ variables; exit with the field name on any problem."""
        source = read_env() if env is None else env
        raw = {
            name: value
            for key, value in source.items()
            if key.startswith(PREFIX)
            and (name := key.removeprefix(PREFIX).lower()) not in OTHER_TOOLS | IDENTITY | RETIRED
        }
        try:
            settings = msgspec.convert(raw, cls, strict=False)
            if settings.session_idle_hours > settings.session_absolute_hours:
                raise SystemExit("bad setting: session_idle_hours: invalid configuration")

            origins = [origin.strip() for origin in settings.allowed_origins.split(",")]
            try:
                valid = bool(origins) and all(valid_origin(origin) for origin in origins)
            except ValueError:
                valid = False
            if not valid:
                raise SystemExit("bad setting: allowed_origins: invalid configuration")
            if settings.cross_origin_browser and any(
                not origin.startswith("https://") for origin in origins
            ):
                raise SystemExit("bad setting: allowed_origins: invalid configuration")
            validate_models(settings, access=False)
            validate_mcp_url(settings.mcp_url)
            validate_oidc(settings)
            validate_hub(settings)
            if not settings.queue_names():
                raise SystemExit("bad setting: worker_queues: invalid configuration")
            return settings
        except msgspec.ValidationError as error:
            message = str(error)
            match = re.search(r" - at `\$\.([a-z_]+)`$", message)
            if match is None:
                match = re.fullmatch(
                    r"Object (?:contains unknown|missing required) field `([a-z_]+)`", message
                )
            field = match[1] if match else "configuration"
            raise SystemExit(f"bad setting: {field}: invalid configuration") from None

    def queue_names(self) -> list[str]:
        """The worker's queues in the order given; empty when any name is unknown."""
        names = [name.strip() for name in self.worker_queues.split(",") if name.strip()]
        return names if names and set(names) <= set(QUEUES) else []

    def concurrency_for(self, queues: list[str] | None = None) -> int:
        """Jobs at once for these queues: one queue's setting, or the min when several."""
        names = queues if queues is not None else self.queue_names()
        values = {
            "agent": self.worker_concurrency_agent,
            "interactive": self.worker_concurrency_interactive,
            "bulk": self.worker_concurrency_bulk,
        }
        return min(values[name] for name in names)

    def __repr__(self) -> str:
        return f"Settings(database={self.database_host}:{self.database_port}/{self.database_name})"

    __str__ = __repr__


def validate_oidc(settings: Settings) -> None:
    """https addresses, or http on this machine; the client ID and secret come with an authority."""
    from urllib.parse import urlsplit

    def web(value: str) -> bool:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        return (
            (parsed.scheme == "https" or local)
            and bool(parsed.hostname)
            and parsed.username is None
            and not parsed.query
            and not parsed.fragment
        )

    if not settings.oidc_authority:
        return
    if not web(settings.oidc_authority) or settings.oidc_authority.endswith("/"):
        raise SystemExit("bad setting: oidc_authority: invalid configuration")
    for field in ["oidc_client_id", "oidc_client_secret"]:
        if not getattr(settings, field).strip():
            raise SystemExit(f"bad setting: {field}: invalid configuration")
    if not web(settings.oidc_redirect_uri):
        raise SystemExit("bad setting: oidc_redirect_uri: invalid configuration")


def validate_hub(settings: Settings) -> None:
    """Plain http(s) origins; a Hub address needs the service token."""
    from urllib.parse import urlsplit

    for field in ["hub_url", "hub_public_url"]:
        value = getattr(settings, field)
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise SystemExit(f"bad setting: {field}: invalid configuration")
    if settings.hub_url and not settings.hub_token.strip():
        raise SystemExit("bad setting: hub_token: invalid configuration")


def validate_mcp_url(value: str) -> None:
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and parsed.path in {"/mcp", "/mcp/"}
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and (parsed.port is None or 0 < parsed.port <= 65535)
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise SystemExit("bad setting: mcp_url: invalid configuration")


def validate_models(settings: Settings, *, access: bool = True) -> None:
    """Check model settings; with access, the endpoint and key must be configured.

    Startup checks without access so a server with no model settings still serves
    sign-in, documents and the datacube; any model call then refuses."""
    import os
    from urllib.parse import urlsplit

    def fail(field: str):
        raise SystemExit(f"bad setting: {field}: invalid configuration")

    for field in ["model_main", "model_check", "model_embedding"]:
        value = getattr(settings, field)
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", value
        ):
            fail(field)
    budget = settings.model_max_output_tokens
    if not isinstance(budget, int) or isinstance(budget, bool) or not 16 <= budget <= 16000:
        fail("model_max_output_tokens")
    if not access and not settings.model_key and not settings.model_endpoint:
        return
    if os.environ.get("AZURE_AI_PROJECTS_CONSOLE_LOGGING", "false").lower() == "true":
        fail("model_logging")
    if not settings.model_key or not settings.model_key.strip():
        fail("model_key")
    try:
        endpoint = urlsplit(settings.model_endpoint)
        valid = (
            endpoint.scheme == "https"
            and bool(endpoint.hostname)
            and endpoint.username is None
            and endpoint.password is None
            and not endpoint.query
            and not endpoint.fragment
            and (endpoint.port is None or 0 < endpoint.port <= 65535)
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        fail("model_endpoint")


def valid_origin(origin: str) -> bool:
    from urllib.parse import urlsplit

    parsed = urlsplit(origin)
    port = parsed.port
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and (port is None or 0 < port <= 65535)
    )
