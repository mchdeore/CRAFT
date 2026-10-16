"""Typed, validated settings. A bad value stops startup and names the field, never the value."""

import re
from collections.abc import Mapping
from typing import Annotated, Literal

import msgspec

from app.infrastructure.config.env import read_env

PREFIX = "CRAFT_"
OTHER_TOOLS = {"db_admin_password", "owner_db_password"}  # for initdb.sh and Alembic, not the app


class Settings(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "craft_dev"
    provider_root: str = "var/provider"
    session_idle_hours: Annotated[int, msgspec.Meta(gt=0)] = 8
    session_absolute_hours: Annotated[int, msgspec.Meta(gt=0)] = 12
    sign_in_attempts_per_minute: Annotated[int, msgspec.Meta(gt=0)] = 5
    password_min_length: Annotated[int, msgspec.Meta(ge=12)] = 12
    allowed_origins: str = "http://localhost:8000"
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
