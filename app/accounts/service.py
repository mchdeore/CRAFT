"""Short transactions for local account administration."""

from sqlalchemy import select

from app.infrastructure.access import Caller
from app.infrastructure.database.metadata import metadata
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import InvalidInput, NotFound
from app.infrastructure.grants import insert_grant, remove_grant
from app.infrastructure.provider.simulated import SILOS


def change(caller: Caller, request, remove: bool) -> None:
    if caller.sessions is None:
        raise NotFound()
    if request.silo is not None and request.silo not in SILOS:
        raise InvalidInput()
    with unit_of_work(caller.sessions) as session:
        users = metadata.tables["users"]
        user = session.execute(
            select(users.c.id).where(users.c.username == request.username.lower())
        ).scalar_one_or_none()
        if user is None:
            raise NotFound()
        if remove:
            remove_grant(session, user, request.mission, request.silo)
        else:
            insert_grant(session, user, request.mission, request.silo)
