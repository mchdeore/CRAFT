"""CRAFT backend. create_app composes everything; importing does no work."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.accounts import handlers as account_handlers
from app.health.handlers import migration_head
from app.infrastructure.config.settings import Settings
from app.infrastructure.database.engine import make_engine, make_sessions
from app.infrastructure.errors import install_error_handlers
from app.infrastructure.logging import configure_logging, instrument_sql
from app.infrastructure.operations.pipeline import register_http
from app.infrastructure.paths import ROOT
from app.infrastructure.requests import RequestIdMiddleware
from app.library import handlers as library_handlers


class DevPage(StaticFiles):
    """Expose only the single development page, never neighboring source files."""

    async def get_response(self, path: str, scope: Scope):
        if path != ".":
            raise HTTPException(status_code=404)
        return await super().get_response("dev.html", scope)


def close_on_exit(engine: Engine) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    return lifespan


def create_app(settings: Settings) -> FastAPI:
    """Build the app: logging, the pool, error bodies, health routes, request IDs."""
    _ = library_handlers, account_handlers
    configure_logging(settings.log_level)
    engine = make_engine(settings)
    instrument_sql(engine)
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, lifespan=close_on_exit(engine))
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = make_sessions(engine)
    app.state.migration_head = migration_head()
    install_error_handlers(app)
    register_http(app)
    if settings.dev_page:
        app.mount("/dev", DevPage(directory=ROOT / "app/dev"))
    app.add_middleware(RequestIdMiddleware)
    return app
