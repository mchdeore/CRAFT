"""Typed, validated settings. A bad value stops startup and names the field, never the value."""

import re
from collections.abc import Mapping
from typing import Annotated, Literal

import msgspec

from app.infrastructure.config.env import read_env

PREFIX = "CRAFT_"
OTHER_TOOLS = {"db_admin_password", "owner_db_password", "mcp_account"}  # setup and Compose only


class Settings(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "craft_dev"
    provider_root: str = "var/provider"
    process_root: str = ""
    session_idle_hours: Annotated[int, msgspec.Meta(gt=0)] = 8
    session_absolute_hours: Annotated[int, msgspec.Meta(gt=0)] = 12
    sign_in_attempts_per_minute: Annotated[int, msgspec.Meta(gt=0)] = 5
    password_min_length: Annotated[int, msgspec.Meta(ge=1)] = 12
    allowed_origins: str = "http://localhost:8000"
    cross_origin_browser: bool = False
    dev_page: bool = False
    model_main: str = "gpt-5.2"
    model_check: str = "gpt-5.2"
    model_embedding: str = "text-embedding-3-large"
    model_endpoint: str = ""
    model_key: str = ""
    model_max_output_tokens: Annotated[int, msgspec.Meta(ge=16, le=16000)] = 4000
    agent_run_seconds: Annotated[int, msgspec.Meta(ge=30, le=900)] = 120
    wolfram_app_id: str = ""
    mcp_url: str = "http://127.0.0.1:8000/mcp"
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
            and (name := key.removeprefix(PREFIX).lower()) not in OTHER_TOOLS
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

    def __repr__(self) -> str:
        return f"Settings(database={self.database_host}:{self.database_port}/{self.database_name})"

    __str__ = __repr__


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
