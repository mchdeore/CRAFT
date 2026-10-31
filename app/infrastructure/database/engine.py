"""One connection pool, as craft_app. Connect and statement timeouts are 3 s."""

from typing import Any

from psycopg.conninfo import make_conninfo
from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.config.env import owner_password
from app.infrastructure.config.settings import Settings

# The request pools' limits (the app's and craft-id's); owner tools and migrations run longer.
CONNECT_ARGS = {"connect_timeout": 3, "options": "-c statement_timeout=3000"}


def database_url(settings: Any, login: str, password: str, database: str | None = None) -> URL:
    """The settings' database (or `database`) as one login. settings: app or craft-id settings."""
    return URL.create(
        "postgresql+psycopg",
        username=login,
        password=password,
        host=settings.database_host,
        port=settings.database_port,
        database=database or settings.database_name,
    )


def make_engine(settings: Settings) -> Engine:
    """The app's pool. It never connects at creation, so the app starts with the database down."""
    url = database_url(settings, "craft_app", settings.app_db_password)
    return create_engine(url, pool_pre_ping=True, connect_args=CONNECT_ARGS)


def conninfo(settings: Settings) -> str:
    """The same login as the pool, for a single direct connection (health check)."""
    return make_conninfo(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user="craft_app",
        password=settings.app_db_password,
        connect_timeout=3,
    )


def make_sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def make_owner_sessions(settings: Settings):
    """Explicit owner pool for seed and index orchestration, never application requests."""
    engine = create_engine(database_url(settings, "craft_owner", owner_password()))
    return sessionmaker(engine, expire_on_commit=False), engine
