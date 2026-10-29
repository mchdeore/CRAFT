"""The daily clean-up: rows nothing reads any more go, as one scheduled job on the bulk queue."""

from app.accounts.handlers import prune_expired
from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.operations import Job, operation
from app.jobs.handlers import prune_progress
from app.notifications.handlers import prune_read

PROGRESS_DAYS = 30
READ_NOTIFICATION_DAYS = 90


@operation(PUBLIC, job=Job(queue="bulk", retry=False, every="17 3 * * *"))
def prune(caller: Caller) -> None:
    """Delete expired workspace tokens and sign-in sessions, old job progress and old read
    notifications. (Agent run events keep their own one-day clean-up.)"""
    assert caller.sessions is not None
    with unit_of_work(caller.sessions) as session:
        prune_expired(session)
        prune_progress(session, PROGRESS_DAYS)
        prune_read(session, READ_NOTIFICATION_DAYS)
