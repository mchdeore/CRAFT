"""Call state follows thread-pool work and is restored after nested operations."""

from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import UUID

from app.infrastructure.access import Caller


@dataclass(slots=True)
class CallContext:
    request_id: UUID
    call_id: UUID
    parent_call_id: UUID | None
    caller: Caller
    started: float
    db_statements: int = 0
    db_ms: float = 0


current_call: ContextVar[CallContext | None] = ContextVar("current_call", default=None)
transport_request: ContextVar[Any] = ContextVar("transport_request", default=None)
sql_started: ContextVar[tuple[float, ...]] = ContextVar("sql_started", default=())


def start_statement() -> None:
    sql_started.set((*sql_started.get(), perf_counter()))


def end_statement() -> None:
    times = sql_started.get()
    if not times:
        return
    sql_started.set(times[:-1])
    current = current_call.get()
    if current is not None:
        current.db_statements += 1
        current.db_ms += (perf_counter() - times[-1]) * 1000
