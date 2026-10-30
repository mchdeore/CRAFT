"""CRAFT backend. create_app composes everything; importing does no work."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from importlib import import_module

from fastapi import FastAPI
from sqlalchemy import Engine
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.infrastructure.config.settings import Settings
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.errors import install_error_handlers
from app.infrastructure.logging import configure_logging, instrument_sql
from app.infrastructure.operations.pipeline import register_http
from app.infrastructure.paths import ROOT
from app.infrastructure.requests import RequestIdMiddleware
from app.infrastructure.traffic import TrafficMiddleware

# Modules that declare operations: importing one registers its operations.
OPERATION_MODULES = (
    "app.accounts.handlers",
    "app.agent.handlers",
    "app.agent.jobs",
    "app.approvals.handlers",
    "app.conversations.handlers",
    "app.cube.handlers",
    "app.health.handlers",
    "app.jobs.handlers",
    "app.library.handlers",
    "app.notes.handlers",
    "app.notifications.handlers",
    "app.skills.handlers",
    "app.summarize.handlers",
    "app.tools.handlers",
    "app.workspace.handlers",
)


def load_operations() -> None:
    """Fill the operation registry. Whatever reads it (the app, worker, CLI) calls this first."""
    for module in OPERATION_MODULES:
        import_module(module)


class SummaryPage(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        scope.setdefault("state", {})["operation"] = "summarize.page"
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
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            async with mcp.run():
                yield
        finally:
            engine.dispose()

    return lifespan


def create_app(settings: Settings) -> FastAPI:
    """Build the app: logging, the pool, error bodies, health routes, request IDs."""
    load_operations()
    from app.health.handlers import migration_head
    from app.infrastructure.mcp.server import HttpTransport
    from app.infrastructure.operations.registry import registry

    configure_logging(settings.log_level)
    engine = make_engine(settings)
    instrument_sql(engine)
    mcp = HttpTransport(registry)
    app = FastAPI(
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=close_on_exit(engine, mcp),
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = make_sessions(engine)
    app.state.migration_head = migration_head()
    install_error_handlers(app)
    register_http(app)
    app.mount("/mcp", mcp)
    app.mount("/summarize", SummaryPage(directory=ROOT / "app/summarize"))
    app.add_middleware(MountPathMiddleware)
    if settings.cross_origin_browser:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[origin.strip() for origin in settings.allowed_origins.split(",")],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "Accept", "X-Request-ID", "X-File-Name", "X-Simulated"],
            expose_headers=["Content-Disposition", "X-Request-ID"],
        )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(TrafficMiddleware, sessions=app.state.sessions)
    return app
