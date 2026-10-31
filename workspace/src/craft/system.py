"""The native tools the package uses: LibreOffice for Office rendering, Poppler for page images.

They are installed in the workspace image; elsewhere ``craft.check()`` says what is
missing and how to install it. The kernel's guard refuses program launches, so
``run`` lets one launch of a tool found here past it, on the calling thread only.
"""

import importlib.util
import os
import shutil
import signal
import subprocess
import sys
from contextlib import contextmanager
from typing import Any

_TOOLS: dict[str, dict[str, Any]] = {
    "soffice": {
        "name": "LibreOffice",
        "paths": (
            "/usr/lib/libreoffice/program/soffice",
            "/opt/libreoffice/program/soffice",
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        ),
        "install": "apt-get install libreoffice-writer-nogui libreoffice-calc-nogui "
        "libreoffice-impress-nogui (Debian or Ubuntu), or brew install --cask libreoffice (macOS)",
    },
    "pdftoppm": {
        "name": "Poppler",
        "paths": ("/usr/bin/pdftoppm", "/opt/homebrew/bin/pdftoppm", "/usr/local/bin/pdftoppm"),
        "install": "apt-get install poppler-utils (Debian or Ubuntu), "
        "or brew install poppler (macOS)",
    },
}
_FONTS = ("Carlito", "Caladea", "Liberation Sans")
_FONT_INSTALL = "apt-get install fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation2"
_LIBRARIES = {
    "pandas": "pandas",
    "matplotlib": "matplotlib",
    "duckdb": "duckdb",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "openpyxl": "openpyxl",
    "pypdf": "pypdf",
    "vl_convert": "vl-convert-python",
}
_found: dict[str, str] = {}


class MissingSoftware(RuntimeError):
    """A native tool the package needs is not installed; the message says what to install."""


def find(tool: str) -> str:
    """The full path of ``soffice`` or ``pdftoppm``: on ``PATH``, then the usual install places."""
    if tool not in _found:
        spec = _TOOLS[tool]
        located = shutil.which(tool) or next(
            (path for path in spec["paths"] if os.access(path, os.X_OK)), None
        )
        if located is None:
            raise MissingSoftware(
                f"{spec['name']} ({tool}) is not installed here; the CRAFT workspace image "
                f"has it. To install it: {spec['install']}"
            )
        _found[tool] = located
    return _found[tool]


def run(tool: str, *args: str, timeout: float) -> subprocess.CompletedProcess:
    """Run a found tool without a shell; a failure raises with the end of its error output."""
    result = _launch([find(tool), *args], timeout)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()[-500:]
        raise RuntimeError(f"{_TOOLS[tool]['name']} failed: {detail or 'no error output'}")
    return result


def check() -> dict[str, str]:
    """Print and return what works and what is missing: tools, fonts, libraries and CRAFT."""
    report = {spec["name"]: _tool_status(tool) for tool, spec in _TOOLS.items()}
    installed = _font_families()
    missing = [font for font in _FONTS if font.lower() not in installed]
    report["Fonts"] = (
        "ok: " + ", ".join(_FONTS)
        if not missing
        else f"missing {', '.join(missing)}: {_FONT_INSTALL}"
    )
    absent = sorted(
        package for module, package in _LIBRARIES.items() if not importlib.util.find_spec(module)
    )
    report["Python libraries"] = "ok" if not absent else "missing: pip install " + " ".join(absent)
    report["Access points"] = _access_status()
    width = max(len(name) for name in report)
    for name, status in report.items():
        print(f"{name:<{width}}  {status}")
    return report


def _tool_status(tool: str) -> str:
    try:
        # pdftoppm prints its version to standard error and may exit non-zero doing it.
        result = _launch([find(tool), "-v" if tool == "pdftoppm" else "--version"], 60)
    except MissingSoftware as error:
        return f"missing: {error}"
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"not working: {error}"
    lines = (result.stdout or result.stderr).decode(errors="replace").strip().splitlines()
    return "ok: " + (lines[0] if lines else _found[tool])


def _font_families() -> set[str]:
    located = shutil.which("fc-list")
    if located is None:
        return set()
    try:
        output = _launch([located, ":", "family"], 60).stdout.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {name.strip().lower() for line in output.splitlines() for name in line.split(",")}


def _access_status() -> str:
    from craft import access

    try:
        who = access.whoami().data
    except Exception as error:  # an unset address, no token or an unreachable server
        return f"not working: {error}"
    return "ok: signed in as " + str(who.get("username") or who.get("id"))


def _launch(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run in a process group of its own, so a timeout also stops what the tool started, such
    as LibreOffice's soffice.bin, which would otherwise keep its profile locked."""
    with _guard_paused():
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
        ) as process:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


@contextmanager
def _guard_paused():
    guard = sys.modules.get("craft_guard")
    if guard is None:
        yield
        return
    guard._local.bypass = True
    try:
        yield
    finally:
        guard._local.bypass = False
