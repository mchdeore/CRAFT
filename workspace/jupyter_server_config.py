"""Dedicated user server config. Loaded by Jupyter, never by the CRAFT service."""

import os
from pathlib import Path
from urllib.parse import urlsplit

root = Path(os.environ.get("USER_ROOT", "/workspace"))
# Under JupyterHub (deploy/jupyterhub) the Hub signs people and CRAFT in; otherwise a token.
hub = bool(os.environ.get("JUPYTERHUB_API_TOKEN"))
token = os.environ.get("JUPYTER_TOKEN", "")
origin = os.environ.get("CRAFT_UI_ORIGIN", "http://127.0.0.1:5173")
parsed = urlsplit(origin)
if not root.is_absolute() or not root.is_dir() or root.resolve() != root:
    raise ValueError("USER_ROOT must be an existing owned mount without symlinks")
if not hub and not token.strip():
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

# Starter skills are copied once; an existing file with the same name is never replaced.
starter = Path(__file__).with_name("skills")
if starter.is_dir():
    skills = root / "skills"
    if not skills.is_symlink():
        skills.mkdir(exist_ok=True)
        for source in sorted(starter.glob("*.md")):
            target = skills / source.name
            if not target.exists() and not target.is_symlink():
                target.write_bytes(source.read_bytes())

# The notebook control server beside JupyterLab in the pod reads notebooks with this server's
# own Hub token, which reaches this server only. The folder is a pod-local memory volume.
control = Path(os.environ.get("CRAFT_CONTROL_DIR", "/run/craft-control"))
if hub and control.is_dir():
    for name, value in [
        ("token", os.environ["JUPYTERHUB_API_TOKEN"]),
        ("url", "http://127.0.0.1:8888" + os.environ.get("JUPYTERHUB_SERVICE_PREFIX", "/")),
    ]:
        path = control / name
        path.unlink(missing_ok=True)
        path.touch(mode=0o600)
        path.write_text(value)

# Terminal shells start guarded, like kernels; the server process itself is not.
c_terminal_env = {"CRAFT_GUARD": "1"}

# Jupyter's config loader injects this callable into the execution globals.
c = globals()["get_config"]()
c.ServerApp.root_dir = str(root)
# Office previews: the PDF renderer behind JupyterLab's Preview tab.
c.ServerApp.jpserver_extensions = {"craft_preview": True}
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
if not hub:
    c.IdentityProvider.token = token
password_hash = os.environ.get("JUPYTER_PASSWORD_HASH", "")
if password_hash:
    c.PasswordIdentityProvider.hashed_password = password_hash
c.FileContentsManager.allow_hidden = False
c.Application.log_level = 30
c.ServerApp.terminado_settings = {
    "shell_command": ["/usr/bin/env", *(f"{k}={v}" for k, v in c_terminal_env.items()), "/bin/bash"]
}
