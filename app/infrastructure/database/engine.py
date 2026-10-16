"""One connection pool, as craft_app. Connect and statement timeouts are 3 s."""

from psycopg.conninfo import make_conninfo
from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.config.settings import Settings


def make_engine(settings: Settings) -> Engine:
    """The app's pool. It never connects at creation, so the app starts with the database down."""
    url = URL.create(
        "postgresql+psycopg",
        username="craft_app",
        password=settings.app_db_password,
        host=settings.database_host,
        port=settings.database_port,
        database=settings.database_name,
    )
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000"},
    )


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
