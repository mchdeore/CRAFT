"""Alembic runs as craft_owner (never the app's login), against the database in the settings.
Tests pass another database name through config.attributes["database_name"]."""

from alembic import context
from sqlalchemy import create_engine

from app.infrastructure.config.env import owner_password
from app.infrastructure.config.settings import Settings
from app.infrastructure.database.engine import database_url
from app.infrastructure.database.metadata import include_name, metadata

settings = Settings.from_env()
url = database_url(
    settings, "craft_owner", owner_password(), context.config.attributes.get("database_name")
)


def migrate(connection):
    context.configure(connection=connection, target_metadata=metadata, include_name=include_name)
    with context.begin_transaction():
        context.run_migrations()


if connection := context.config.attributes.get("connection"):
    migrate(connection)
else:
    with create_engine(url).connect() as connection:
        migrate(connection)
