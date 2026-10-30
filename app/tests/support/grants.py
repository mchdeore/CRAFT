"""What craft_app may do to each table."""

from sqlalchemy import Engine, text


def assert_grants(engine: Engine, expected: dict[str, set[str]]) -> None:
    with engine.connect() as connection:
        for table, rights in expected.items():
            actual = {
                right
                for right in [
                    "SELECT",
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "TRUNCATE",
                    "REFERENCES",
                    "TRIGGER",
                ]
                if connection.execute(
                    text("select has_table_privilege('craft_app', :table, :right)"),
                    {"table": table, "right": right},
                ).scalar()
            }
            assert actual == rights, table
        assert not connection.execute(
            text("select has_schema_privilege('craft_app', 'public', 'CREATE')")
        ).scalar()
