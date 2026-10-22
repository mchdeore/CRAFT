"""Alembic runs as craft_owner (never the app's login), against the database in the settings.
Tests pass another database name through config.attributes["database_name"]."""

from alembic import context
from sqlalchemy import URL, create_engine

from app.infrastructure.config.env import read_env
from app.infrastructure.config.settings import Settings
from app.infrastructure.database.metadata import metadata

settings = Settings.from_env()
owner_password = read_env().get("CRAFT_OWNER_DB_PASSWORD")
if not owner_password:
    raise SystemExit("bad setting: CRAFT_OWNER_DB_PASSWORD is required for migrations")

url = URL.create(
    "postgresql+psycopg",
    username="craft_owner",
    password=owner_password,
    host=settings.database_host,
    port=settings.database_port,
    database=context.config.attributes.get("database_name", settings.database_name),
)


def migrate(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


if connection := context.config.attributes.get("connection"):
    migrate(connection)
else:
    with create_engine(url).connect() as connection:
        migrate(connection)
