"""CRAFT backend. create_app composes everything; importing does no work."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.accounts import handlers as account_handlers
from app.agent import handlers as agent_handlers
from app.conversations import handlers as conversation_handlers
from app.cube import handlers as cube_handlers
from app.health.handlers import migration_head
from app.infrastructure.config.settings import Settings
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.errors import install_error_handlers
from app.infrastructure.logging import configure_logging, instrument_sql
from app.infrastructure.operations.pipeline import register_http
from app.infrastructure.paths import ROOT
from app.infrastructure.requests import RequestIdMiddleware
from app.library import handlers as library_handlers
from app.summarize import handlers as summarize_handlers


class DevPage(StaticFiles):
    """Expose only the single development page, never neighboring source files."""

    async def get_response(self, path: str, scope: Scope):
        if path != ".":
            raise HTTPException(status_code=404)
        return await super().get_response("dev.html", scope)


class SummaryPage(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        if path != ".":
            raise HTTPException(status_code=404)
        response = await super().get_response("page.html", scope)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; frame-src 'self' blob:; "
            "object-src 'none'; base-uri 'none'; form-action 'self'"
        )
        return response


class MountPathMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] in {"/summarize", "/mcp"}:
            path = scope["path"] + "/"
            scope = dict(scope, path=path, raw_path=path.encode())
        await self.app(scope, receive, send)


def close_on_exit(engine: Engine, mcp) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            async with mcp.run():
                yield
        finally:
            engine.dispose()

    return lifespan


def create_app(settings: Settings) -> FastAPI:
    """Build the app: logging, the pool, error bodies, health routes, request IDs."""
    _ = (
        library_handlers,
        account_handlers,
        cube_handlers,
        summarize_handlers,
        agent_handlers,
        conversation_handlers,
    )
    configure_logging(settings.log_level)
    engine = make_engine(settings)
    instrument_sql(engine)
    from app.infrastructure.mcp.server import HttpTransport
    from app.infrastructure.operations.registry import registry

    mcp = HttpTransport(registry)
    app = FastAPI(
        openapi_url=None, docs_url=None, redoc_url=None, lifespan=close_on_exit(engine, mcp)
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = make_sessions(engine)
    app.state.migration_head = migration_head()
    install_error_handlers(app)
    register_http(app)
    app.mount("/mcp", mcp)
    if settings.dev_page:
        app.mount("/dev", DevPage(directory=ROOT / "app/dev"))
    app.mount("/summarize", SummaryPage(directory=ROOT / "app/summarize"))
    app.add_middleware(MountPathMiddleware)
    app.add_middleware(RequestIdMiddleware)
    return app
