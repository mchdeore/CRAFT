"""One short transaction: all saved or none."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

_approved_transaction: ContextVar[tuple[sessionmaker[Session], Session] | None] = ContextVar(
    "approved_transaction", default=None
)


@contextmanager
def approved_transaction(sessions: sessionmaker[Session], session: Session):
    """Join one server-owned transaction while an approved database operation runs."""
    token = _approved_transaction.set((sessions, session))
    try:
        yield
    finally:
        _approved_transaction.reset(token)


@contextmanager
def unit_of_work(sessions: sessionmaker[Session], *, read_only: bool = False) -> Iterator[Session]:
    """Commit on success, roll back on error, always close. read_only: PostgreSQL refuses writes."""
    shared = _approved_transaction.get()
    if shared is not None and shared[0] is sessions:
        yield shared[1]
        return
    with sessions() as session, session.begin():
        if read_only:
            session.execute(text("set transaction read only"))
        yield session
