"""The daily clean-up: rows nothing reads any more go, as one scheduled job on the bulk queue."""

from sqlalchemy import text

from app.accounts.handlers import prune_expired
from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.operations import Job, operation
from app.infrastructure.traffic import prune as prune_traffic
from app.jobs.handlers import prune_history, prune_progress
from app.notifications.handlers import prune_read

PROGRESS_DAYS = 30
READ_NOTIFICATION_DAYS = 90
# Ended jobs and queue samples feed `craft queues report`; traffic counts feed usage reviews.
JOB_DAYS = SAMPLE_DAYS = 30
TRAFFIC_DAYS = 90


@operation(PUBLIC, job=Job(queue="bulk", every="17 3 * * *"))
def prune(caller: Caller) -> None:
    """Delete expired workspace tokens and sign-in sessions, old job progress, ended jobs and
    queue samples, traffic counts and old read notifications. (Agent run events keep their own
    one-day clean-up; call_log is kept whole.)"""
    assert caller.sessions is not None
    with unit_of_work(caller.sessions) as session:
        # Nobody waits on this job, and a backlog can take longer than a request's 3 s limit.
        session.execute(text("set local statement_timeout = '5min'"))
        prune_expired(session)
        prune_progress(session, PROGRESS_DAYS)
        prune_history(session, JOB_DAYS, SAMPLE_DAYS)
        prune_traffic(session, TRAFFIC_DAYS)
        prune_read(session, READ_NOTIFICATION_DAYS)
