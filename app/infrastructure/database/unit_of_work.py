"""One short transaction: all saved or none."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

_approved_transaction: ContextVar[tuple[sessionmaker[Session], Session] | None] = ContextVar(
    "approved_transaction", default=None
)
_ambient_transactions: ContextVar[int] = ContextVar("ambient_transactions", default=0)


def has_ambient_transaction() -> bool:
    return _ambient_transactions.get() > 0


@contextmanager
def approved_transaction(sessions: sessionmaker[Session], session: Session):
    """Join one server-owned transaction while an approved database operation runs."""
    token = _approved_transaction.set((sessions, session))
    ambient = _ambient_transactions.set(_ambient_transactions.get() + 1)
    try:
        yield
    finally:
        _approved_transaction.reset(token)
        _ambient_transactions.reset(ambient)


@contextmanager
def unit_of_work(sessions: sessionmaker[Session], *, read_only: bool = False) -> Iterator[Session]:
    """Commit on success, roll back on error, always close. read_only: PostgreSQL refuses writes."""
    token = _ambient_transactions.set(_ambient_transactions.get() + 1)
    try:
        shared = _approved_transaction.get()
        if shared is not None and shared[0] is sessions:
            yield shared[1]
            return
        with sessions() as session, session.begin():
            if read_only:
                session.execute(text("set transaction read only"))
            yield session
    finally:
        _ambient_transactions.reset(token)
