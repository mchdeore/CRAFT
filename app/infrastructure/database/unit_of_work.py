"""One short transaction: all saved or none."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


@contextmanager
def unit_of_work(sessions: sessionmaker[Session], *, read_only: bool = False) -> Iterator[Session]:
    """Commit on success, roll back on error, always close. read_only: PostgreSQL refuses writes."""
    with sessions() as session, session.begin():
        if read_only:
            session.execute(text("set transaction read only"))
        yield session
