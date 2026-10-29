"""CRAFT backend. create_app composes everything; importing does no work."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.accounts import handlers as account_handlers
from app.agent import handlers as agent_handlers
from app.agent import jobs as agent_jobs
from app.approvals import handlers as approval_handlers
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
from app.jobs import handlers as job_handlers
from app.library import handlers as library_handlers
from app.notes import handlers as note_handlers
from app.notifications import handlers as notification_handlers
from app.skills import handlers as skill_handlers
from app.summarize import handlers as summarize_handlers
from app.tools import handlers as tool_handlers
from app.workspace import handlers as workspace_handlers


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


def close_on_exit(
    engine: Engine, mcp, settings: Settings
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from anyio import CancelScope, to_thread

        from app.infrastructure.processes import open_processes

        processes = None
        try:
            processes = await to_thread.run_sync(open_processes, settings)
            app.state.processes = processes
            async with mcp.run():
                yield
        finally:
            try:
                if processes is not None:
                    with CancelScope(shield=True):
                        await to_thread.run_sync(processes.close)
            finally:
                engine.dispose()

    return lifespan


def create_app(settings: Settings) -> FastAPI:
    """Build the app: logging, the pool, error bodies, health routes, request IDs."""
    _ = (
        library_handlers,
        job_handlers,
        account_handlers,
        approval_handlers,
        cube_handlers,
        summarize_handlers,
        agent_handlers,
        agent_jobs,
        conversation_handlers,
        note_handlers,
        notification_handlers,
        skill_handlers,
        tool_handlers,
        workspace_handlers,
    )
    configure_logging(settings.log_level)
    engine = make_engine(settings)
    instrument_sql(engine)
    from app.infrastructure.mcp.server import HttpTransport
    from app.infrastructure.operations.registry import registry

    mcp = HttpTransport(registry)
    app = FastAPI(
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=close_on_exit(engine, mcp, settings),
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
    return app
