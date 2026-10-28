"""Office previews in JupyterLab: the server renders a file's PDF; the front end shows it.

``GET /craft/preview?path=<relative name>`` returns the PDF of a Word, Excel or
PowerPoint file under ``USER_ROOT``. Results are cached by the file's SHA-256, one
conversion runs at a time, and each is limited to 120 seconds. CRAFT's API is not involved.
"""

import asyncio
import hashlib
import tempfile
from pathlib import Path

# Jupyter Server is in the workspace image, not in CRAFT's own environment.
from jupyter_server.base.handlers import APIHandler  # pyright: ignore[reportMissingImports]
from jupyter_server.utils import url_path_join  # pyright: ignore[reportMissingImports]
from tornado import web

SUFFIXES = {".docx", ".xlsx", ".pptx"}
MAX_BYTES = 25 * 1024 * 1024  # CRAFT's file limit
CACHE = Path(tempfile.gettempdir(), "craft-preview")
_one_at_a_time = asyncio.Lock()


class TooLarge(Exception):
    pass


def _jupyter_server_extension_points():
    return [{"module": "craft_preview"}]


def _jupyter_labextension_paths():
    return [{"src": "labextension", "dest": "craft-preview"}]


async def preview(name: str) -> bytes:
    """The cached PDF for one owned Office file, rendered once per content hash."""
    from craft import docs, files

    source = files.path(name)
    if source.suffix.lower() not in SUFFIXES or not source.is_file():
        raise FileNotFoundError(name)
    if source.stat().st_size > MAX_BYTES:
        raise TooLarge(name)
    with source.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    cached = CACHE / f"{digest}.pdf"
    if cached.is_file():
        return cached.read_bytes()
    async with _one_at_a_time:
        if not cached.is_file():
            pdf = await asyncio.to_thread(docs.to_pdf, name, timeout=120)
            CACHE.mkdir(mode=0o700, exist_ok=True)
            partial = cached.with_suffix(".part")
            partial.write_bytes(pdf)
            partial.replace(cached)
    return cached.read_bytes()


class PreviewHandler(APIHandler):
    @web.authenticated
    async def get(self):
        name = self.get_query_argument("path", "")
        try:
            pdf = await preview(name)
        except (FileNotFoundError, ValueError):
            raise web.HTTPError(404) from None
        except TooLarge:
            raise web.HTTPError(413, "The file is too large to preview here") from None
        except Exception:
            raise web.HTTPError(502, "The preview could not be rendered") from None
        self.set_header("Content-Type", "application/pdf")
        self.set_header("Content-Disposition", f'inline; filename="{Path(name).stem}.pdf"')
        self.set_header("Cache-Control", "no-store")
        self.finish(pdf)


def _load_jupyter_server_extension(serverapp):
    base = serverapp.web_app.settings["base_url"]
    serverapp.web_app.add_handlers(
        ".*$", [(url_path_join(base, "craft", "preview"), PreviewHandler)]
    )
