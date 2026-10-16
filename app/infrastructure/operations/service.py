"""The orchestration boundary owns the short call-log transaction."""

from typing import Any

from app.infrastructure.access import Caller
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.operations import storage


def record(caller: Caller, fields: dict[str, Any]) -> None:
    if caller.sessions is None:
        return
    with unit_of_work(caller.sessions) as session:
        storage.append_call(session, fields)
