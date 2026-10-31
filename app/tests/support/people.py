"""Generated people: a user with roles, removed with the rows tied to it by foreign key."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, Engine, Table, or_, select, tuple_
from sqlalchemy.schema import sort_tables

from app.infrastructure.database.metadata import metadata


@dataclass(frozen=True)
class Person:
    id: UUID
    username: str


@contextmanager
def person(engine: Engine, roles: tuple[str, ...] = ("member",)) -> Iterator[Person]:
    """A user with these roles. On exit the user is deleted with every row that references it by
    foreign key, directly or through other rows, and its runs' events."""
    username = "person_" + uuid4().hex
    users = metadata.tables["users"]
    with engine.begin() as connection:
        user = connection.execute(
            users.insert()
            .values(username=username, display_name="Generated person")
            .returning(users.c.id)
        ).scalar_one()
        for role in roles:
            connection.execute(
                metadata.tables["user_roles"].insert().values(user_id=user, role=role)
            )
    try:
        yield Person(user, username)
    finally:
        remove(engine, user)


def remove(engine: Engine, user: UUID) -> None:
    """Delete the user's rows, each table's before the rows they reference."""
    owned = owned_rows(user)
    runs, inputs = metadata.tables["agent_runs"], metadata.tables["agent_run_inputs"]
    events = metadata.tables["agent_run_events"]
    with engine.begin() as connection:
        # Run events have no foreign key; they go with the user's runs and run inputs.
        connection.execute(
            events.delete().where(
                or_(
                    events.c.run_id.in_(select(runs.c.id).where(owned[runs])),
                    events.c.run_id.in_(select(inputs.c.run_id).where(owned[inputs])),
                )
            )
        )
        for table, rows in reversed(owned.items()):
            connection.execute(table.delete().where(rows))


def owned_rows(user: UUID) -> dict[Table, ColumnElement[bool]]:
    """For each table, in dependency order, the rows that reference the user directly or through
    other such rows."""
    users = metadata.tables["users"]
    owned: dict[Table, ColumnElement[bool]] = {users: users.c.id == user}
    for table in dependency_order():
        references = [
            tuple_(*key.columns).in_(
                select(*[element.column for element in key.elements]).where(
                    owned[key.referred_table]
                )
            )
            for key in table.foreign_key_constraints
            if key.referred_table in owned and key.referred_table is not table
        ]
        if references:
            owned[table] = or_(*references)
    return owned


def dependency_order() -> list[Table]:
    """Every table after the tables it references: `metadata.sorted_tables`, which leaves out
    foreign keys declared use_alter (messages.run_id), with those added back."""
    tables = sorted(metadata.tables.values(), key=lambda table: table.key)
    late = [
        (key.referred_table, table)
        for table in tables
        for key in table.foreign_key_constraints
        if key.use_alter
    ]
    return sort_tables(tables, extra_dependencies=late)
