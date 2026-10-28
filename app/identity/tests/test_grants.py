"""craft-app's login cannot read craft-id's tables; craft-id's can (when the role exists)."""

import pytest
from sqlalchemy import Engine, text

TABLES = ["id_accounts", "id_credentials", "id_clients", "id_codes"]


def privileges(engine: Engine, role: str) -> dict[str, bool]:
    with engine.connect() as connection:
        if not connection.execute(
            text("select exists (select from pg_roles where rolname = :r)"), {"r": role}
        ).scalar():
            pytest.skip(f"{role} role not created on this database")
        return {
            table: connection.execute(
                text("select has_table_privilege(:r, :t, 'SELECT')"), {"r": role, "t": table}
            ).scalar()
            for table in TABLES
        }


def test_craft_app_cannot_read_identity_tables(owner_engine: Engine) -> None:
    assert not any(privileges(owner_engine, "craft_app").values())


def test_craft_id_reads_its_own_tables(owner_engine: Engine) -> None:
    assert all(privileges(owner_engine, "craft_id").values())
