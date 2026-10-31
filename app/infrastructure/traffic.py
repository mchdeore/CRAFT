"""Bytes in and out for every HTTP request, counted once by one ASGI middleware.

record() is the one place that decides where a count goes. Rows hold no paths, query strings,
tokens or content: only the operation name, the caller's IDs and byte counts."""

from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID

import anyio
import msgspec
from sqlalchemy import UUID as Uuid
from sqlalchemy import BigInteger, Column, DateTime, Identity, Index, Integer, Table, Text, func
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.database.metadata import metadata
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.logging import get_logger

log = get_logger("craft.traffic")

traffic_log = Table(
    "traffic_log",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("request_id", Uuid),
    Column("operation", Text, nullable=False),
    Column("method", Text, nullable=False),
    Column("status", Integer),
    Column("origin", Text),
    Column("user_id", Uuid),
    Column("workspace_token_id", Uuid),
    Column("bytes_in", BigInteger, nullable=False),
    Column("bytes_out", BigInteger, nullable=False),
    Column("duration_ms", Integer, nullable=False),
)
Index("ix_traffic_log_at", traffic_log.c.at)


class Traffic(msgspec.Struct, frozen=True, kw_only=True):
    """One request. bytes_in counts the body the app read (none when refused before reading);
    status is None when no response started (the request was cancelled)."""

    at: datetime
    request_id: UUID | None
    operation: str
    method: str
    status: int | None
    origin: str | None
    user_id: UUID | None
    workspace_token_id: UUID | None
    bytes_in: int
    bytes_out: int
    duration_ms: int


class Counted(Protocol):
    """The caller fields a row keeps, read from scope["state"]["caller"]; access.Caller fits."""

    @property
    def origin(self) -> str: ...
    @property
    def user_id(self) -> UUID | None: ...
    @property
    def workspace_token_id(self) -> UUID | None: ...


def prune(session: Session, days: int) -> None:
    """Delete traffic counted over `days` days ago (craft_app may read only `at`)."""
    session.execute(
        traffic_log.delete().where(
            traffic_log.c.at < func.now() - func.make_interval(0, 0, 0, days)
        )
    )


def record(sessions: sessionmaker[Session], row: Traffic) -> None:
    """Where every count goes: one traffic_log row in its own short transaction."""
    with unit_of_work(sessions) as session:
        # inline(): no RETURNING for the new id, which craft_app may not read (it reads only at).
        session.execute(traffic_log.insert().inline().values(**msgspec.structs.asdict(row)))


class TrafficMiddleware:
    """Outermost, so the last-resort 500 is counted too. Inner layers name the request on
    scope["state"]: "operation" (else "unmatched") and "caller" (the resolved Caller)."""

    def __init__(self, app: Any, sessions: sessionmaker[Session]) -> None:
        self.app, self.sessions = app, sessions

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        # Health probes every few seconds would dominate the table.
        if scope["type"] != "http" or scope["path"].startswith("/health/"):
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})  # shared by every inner copy of the scope
        at, started = datetime.now(UTC), perf_counter()
        bytes_in = bytes_out = 0
        status: int | None = None
        request_id: UUID | None = None

        async def counted_receive() -> dict[str, Any]:
            nonlocal bytes_in
            message = await receive()
            if message["type"] == "http.request":
                bytes_in += len(message.get("body", b""))
            return message

        async def counted_send(message: dict[str, Any]) -> None:
            nonlocal bytes_out, status, request_id
            await send(message)
            if message["type"] == "http.response.start":
                status = message["status"]
                request_id = header_id(message.get("headers", []))
            elif message["type"] == "http.response.body":
                bytes_out += len(message.get("body", b""))

        try:
            await self.app(scope, counted_receive, counted_send)
        finally:
            caller: Counted | None = state.get("caller")
            row = Traffic(
                at=at,
                request_id=request_id,
                operation=state.get("operation", "unmatched"),
                method=scope["method"],
                status=status,
                origin=caller.origin if caller else None,
                user_id=caller.user_id if caller else None,
                workspace_token_id=caller.workspace_token_id if caller else None,
                bytes_in=bytes_in,
                bytes_out=bytes_out,
                duration_ms=round((perf_counter() - started) * 1000),
            )
            with anyio.CancelScope(shield=True):
                try:
                    await anyio.to_thread.run_sync(record, self.sessions, row)
                except Exception as error:  # a lost count never touches the response
                    log.warning(
                        "traffic_record_failed",
                        extra={"fields": {"error_type": type(error).__name__}},
                    )


def header_id(headers: list[tuple[bytes, bytes]]) -> UUID | None:
    for name, value in headers:
        if name.lower() == b"x-request-id":
            try:
                return UUID(value.decode())
            except ValueError:
                return None
    return None
