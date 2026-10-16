"""The table registry: every feature's tables register here (from S1). Alembic compares to it."""

from sqlalchemy import MetaData

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def register_tables() -> None:
    """Import the table declarations without opening a connection."""
    from importlib import import_module

    for module in (
        "app.accounts.storage",
        "app.infrastructure.operations.storage",
        "app.conversations.storage",
        "app.agent.storage",
        "app.approvals.storage",
    ):
        import_module(module)


register_tables()
