"""The orchestration boundary owns the short call-log transactions: appending each call and
counting a personal token's recent calls for its rate limit."""

import math
from typing import Any

from app.infrastructure.access import Caller
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import RateLimited
from app.infrastructure.operations import storage


def record(caller: Caller, fields: dict[str, Any]) -> None:
    """One call_log row in a transaction of its own, never an approved operation's, so a step's
    row stays when that operation rolls back."""
    if caller.sessions is None:
        return
    with caller.sessions() as session, session.begin():
        storage.append_call(session, fields)


def limit_token(caller: Caller, calls: int, seconds: int) -> None:
    """Refuse a personal token that made `calls` top-level calls in the last `seconds`.

    Calls count once logged, when they end, so a client running several at once can go over by
    that many; RateLimited says when the oldest counted call leaves the window."""
    if caller.sessions is None or caller.workspace_token_id is None:
        return
    with unit_of_work(caller.sessions, read_only=True) as session:
        count, leaves = storage.token_calls(session, caller.workspace_token_id, seconds)
    if count >= calls:
        raise RateLimited(retry_after=max(1, math.ceil(leaves.total_seconds())))
