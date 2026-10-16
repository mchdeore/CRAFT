"""Typed, validated settings. A bad value stops startup and names the field, never the value."""

import re
from collections.abc import Mapping
from typing import Literal

import msgspec

from app.infrastructure.config.env import read_env

PREFIX = "CRAFT_"
OTHER_TOOLS = {"db_admin_password", "owner_db_password"}  # for initdb.sh and Alembic, not the app


class Settings(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "craft_dev"
    provider_root: str = "var/provider"
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
            return msgspec.convert(raw, cls, strict=False)  # "5432" → 5432
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
