"""craft-id's settings: the database, its own login, signing key and public address."""

import re
from collections.abc import Mapping
from typing import Literal

import msgspec

from app.infrastructure.config.env import read_env


class IdentitySettings(msgspec.Struct, frozen=True, kw_only=True):
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "craft_dev"
    id_db_password: str = ""
    # RSA private key (PEM). Empty: a new key at each start, so tokens die with it (laptop only).
    id_signing_key: str = ""
    # The address browsers and craft-app use; the issuer is <this>/id/<tenant>/v2.0.
    id_public_url: str = "http://localhost:8100"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "IdentitySettings":
        source = read_env() if env is None else env
        raw = {
            name: value
            for key, value in source.items()
            if key.startswith("CRAFT_")
            and (name := key.removeprefix("CRAFT_").lower()) in cls.__struct_fields__
        }
        try:
            settings = msgspec.convert(raw, cls, strict=False)
        except msgspec.ValidationError as error:
            match = re.search(r" - at `\$\.([a-z_]+)`$", str(error))
            raise SystemExit(
                f"bad setting: {match[1] if match else 'configuration'}: invalid configuration"
            ) from None
        if not re.fullmatch(r"https?://[A-Za-z0-9.:\[\]-]+", settings.id_public_url):
            raise SystemExit("bad setting: id_public_url: invalid configuration")
        return settings

    def __repr__(self) -> str:
        return f"IdentitySettings(public_url={self.id_public_url})"

    __str__ = __repr__
