"""Dedicated user server config. Loaded by Jupyter, never by the CRAFT service."""

import os
from pathlib import Path
from urllib.parse import urlsplit

root = Path(os.environ.get("USER_ROOT", "/workspace"))
token = os.environ.get("JUPYTER_TOKEN", "")
origin = os.environ.get("CRAFT_UI_ORIGIN", "http://127.0.0.1:5173")
parsed = urlsplit(origin)
if not root.is_absolute() or not root.is_dir() or root.resolve() != root:
    raise ValueError("USER_ROOT must be an existing owned mount without symlinks")
if not token.strip():
    raise ValueError("A private per-user JUPYTER_TOKEN is required")
if (
    parsed.scheme not in {"http", "https"}
    or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
    or parsed.username is not None
    or parsed.path not in {"", "/"}
    or parsed.query
    or parsed.fragment
):
    raise ValueError("CRAFT_UI_ORIGIN must be a plain loopback origin")

# Jupyter's config loader injects this callable into the execution globals.
c = globals()["get_config"]()
c.ServerApp.root_dir = str(root)
c.ServerApp.ip = "0.0.0.0"
c.ServerApp.port = 8888
c.ServerApp.port_retries = 0
c.ServerApp.open_browser = False
c.ServerApp.allow_root = False
c.ServerApp.allow_origin = origin.rstrip("/")
c.ServerApp.allow_credentials = True
# Error handling resets prepare-time headers; keep the same exact CORS scope.
c.ServerApp.tornado_settings = {
    "headers": {
        "Content-Security-Policy": f"frame-ancestors 'self' {origin.rstrip('/')}",
        "Access-Control-Allow-Origin": origin.rstrip("/"),
        "Access-Control-Allow-Credentials": "true",
    }
}
c.ServerApp.disable_check_xsrf = False
c.IdentityProvider.token = token
password_hash = os.environ.get("JUPYTER_PASSWORD_HASH", "")
if password_hash:
    c.PasswordIdentityProvider.hashed_password = password_hash
c.FileContentsManager.allow_hidden = False
c.Application.log_level = 30
