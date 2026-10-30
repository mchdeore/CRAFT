"""Callers and call_log reads for operation tests."""

from uuid import uuid4

from fastapi import FastAPI
from sqlalchemy import select

from app.infrastructure.access import Caller
from app.infrastructure.operations.storage import call_log
from app.infrastructure.shapes import Shape


class ProbeInput(Shape):
    value: int


def trusted(app: FastAPI, roles: frozenset[str] = frozenset({"member"})) -> Caller:
    return Caller(
        user_id=uuid4(), roles=roles, sessions=app.state.sessions, settings=app.state.settings
    )


def rows(app: FastAPI, name: str, after: int = 0):
    """This test's call_log rows (ids above `after`) for one operation."""
    with app.state.engine.connect() as connection:
        return (
            connection.execute(
                select(call_log)
                .where(call_log.c.name == name, call_log.c.id > after)
                .order_by(call_log.c.id)
            )
            .mappings()
            .all()
        )
