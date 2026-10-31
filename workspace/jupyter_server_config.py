"""Dedicated user server config. Loaded by Jupyter, never by the CRAFT service."""

import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

root = Path(os.environ.get("USER_ROOT", "/workspace"))
origin = os.environ.get("CRAFT_UI_ORIGIN", "http://127.0.0.1:5173")
parsed = urlsplit(origin)
if not root.is_absolute() or not root.is_dir() or root.resolve() != root:
    raise ValueError("USER_ROOT must be an existing owned mount without symlinks")
# JupyterHub (deploy/jupyterhub) starts this server and signs people and CRAFT in.
if not os.environ.get("JUPYTERHUB_API_TOKEN", "").strip():
    raise ValueError("JUPYTERHUB_API_TOKEN is required: this server runs under JupyterHub")
if (
    parsed.scheme not in {"http", "https"}
    or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
    or parsed.username is not None
    or parsed.path not in {"", "/"}
    or parsed.query
    or parsed.fragment
):
    raise ValueError("CRAFT_UI_ORIGIN must be a plain loopback origin")

# The sha256 of each starter skill version earlier images shipped. When the image changes a
# skill or stops shipping it, add the version it shipped before
# (git show origin/main:workspace/skills/NAME | shasum -a 256), so a volume from before the
# manifest still knows that copy as the image's.
KNOWN_HASHES = {
    "analysis.md": ("47e2986223c1227923235fd12b8ea265a9f1f70da774b3f3e9dec05c6ec4e0e7",),
    "documents.md": (
        "3a35884addfda4c69b5c3789059b73db44a599acf120fe2b1dae94116e480738",
        "f8ba3d5fadfcf9007b1e14f0054a0d573ca9e0f90a368de711ff60bb2a21d9d7",
        "bc9618c9acf41bb135c7eb6237fcbefb08b9474f0ef387f0df4d62499df49feb",
    ),
}


def sync_starter_skills(starter: Path, root: Path) -> None:
    """Keep the image's starter skills in root/skills current without touching the person's edits.

    A hidden manifest holds the sha256 of each copy the image placed. A copy is unmodified when
    it matches that hash or the image's own copy; one the manifest doesn't list, as on a volume
    from before it, may also match an earlier version in KNOWN_HASHES. An unmodified copy is
    updated, or deleted once the image stops shipping it. An edited copy is kept, a copy the
    person deleted is not placed again, and links and folders are never followed or replaced.
    """
    skills = root / "skills"
    if not starter.is_dir() or skills.is_symlink():
        return
    skills.mkdir(exist_ok=True)
    manifest = skills / ".craft-starter.json"
    try:
        placed = json.loads(manifest.read_text())
    except (OSError, ValueError):
        placed = {}  # a volume from before the manifest, or an unreadable one
    if not isinstance(placed, dict):
        placed = {}
    shipped = {source.name: source.read_bytes() for source in starter.glob("*.md")}
    # Names come from the image and the folder itself, so the sync never reaches beyond skills/.
    listed = {path.name for path in skills.glob("*.md") if path.name in placed}
    recorded = {}
    for name in sorted({*shipped, *KNOWN_HASHES, *listed}):
        target, content = skills / name, shipped.get(name)
        new = None if content is None else hashlib.sha256(content).hexdigest()
        missing = not target.exists() and not target.is_symlink()
        old = None
        if target.is_file() and not target.is_symlink():
            old = hashlib.sha256(target.read_bytes()).hexdigest()
        earlier = (placed[name],) if name in placed else KNOWN_HASHES.get(name, ())
        unmodified = old is not None and old in (new, *earlier)
        if content is not None and (unmodified or (missing and name not in placed)):
            if old != new:
                target.write_bytes(content)
            recorded[name] = new
        elif unmodified:
            target.unlink()  # the image no longer ships it
        elif content is not None and name in placed:
            recorded[name] = placed[name]  # edited or deleted by the person: left as it is
    if recorded != placed:
        manifest.write_text(json.dumps(recorded, indent=1, sort_keys=True) + "\n")


# Jupyter only logs an error raised here and then drops this whole file, so a failed sync must
# not raise.
try:
    sync_starter_skills(Path(__file__).with_name("skills"), root)
except OSError as error:
    print(f"Starter skills were not synced: {error}", file=sys.stderr)

# The notebook control server beside JupyterLab in the pod reads notebooks with this server's
# own Hub token, which reaches this server only. The folder is a pod-local memory volume.
control = Path(os.environ.get("CRAFT_CONTROL_DIR", "/run/craft-control"))
if control.is_dir():
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
# Office previews (the PDF renderer behind the Preview tab) and the activity log.
c.ServerApp.jpserver_extensions = {"craft_preview": True, "craft_events": True}
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
c.FileContentsManager.allow_hidden = False
c.Application.log_level = 30
c.ServerApp.terminado_settings = {
    "shell_command": ["/usr/bin/env", *(f"{k}={v}" for k, v in c_terminal_env.items()), "/bin/bash"]
}
