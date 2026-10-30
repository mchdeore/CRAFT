"""Generated people: a user with roles and a password, removed with every row of theirs."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, Engine, Table, or_, select, tuple_

from app.accounts import service as accounts
from app.infrastructure.database.metadata import metadata


@dataclass(frozen=True)
class Person:
    id: UUID
    username: str
    password: str


@contextmanager
def person(
    engine: Engine, roles: tuple[str, ...] = ("member",), password: str | None = None
) -> Iterator[Person]:
    """A user with these roles and a password credential. On exit the user is deleted with
    every row that references it, directly or through other rows."""
    username, password = "person_" + uuid4().hex, password or uuid4().hex
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
        connection.execute(
            metadata.tables["local_credentials"]
            .insert()
            .values(user_id=user, password_hash=accounts.PASSWORDS.hash(password))
        )
    try:
        yield Person(user, username, password)
    finally:
        remove(engine, user)


def remove(engine: Engine, user: UUID) -> None:
    """Delete the user's rows in reverse `metadata.sorted_tables` order: dependents first."""
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
        for table in reversed(metadata.sorted_tables):
            if table in owned:
                connection.execute(table.delete().where(owned[table]))


def owned_rows(user: UUID) -> dict[Table, ColumnElement[bool]]:
    """For each table, which rows reference the user directly or through other such rows.
    `sorted_tables` lists every table after the tables it references."""
    users = metadata.tables["users"]
    owned: dict[Table, ColumnElement[bool]] = {users: users.c.id == user}
    for table in metadata.sorted_tables:
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
