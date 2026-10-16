"""Read CRAFT_ settings from .env, then apply process environment overrides."""

import os

from app.infrastructure.paths import ROOT


def read_env() -> dict[str, str]:
    """Return .env values overridden by the process environment."""
    values: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    values.update(os.environ)
    return values
