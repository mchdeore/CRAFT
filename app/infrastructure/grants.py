"""Mission grants: the three rules that decide what a caller reads, and the grant writes.

A grant names a mission and, optionally, one silo; a grant without a silo covers every silo.
- The document rule (`document_readable`, and `DocumentAccess` for records read outside SQL):
  admins read every document, everyone reads a document outside every mission, and otherwise a
  grant must cover the document's mission and silo.
- The cube rule (`cube_readable`): a grant must cover the mission and silo, for admins too.
- The mission listing (`mission_listed`): any grant on the mission, whatever its silo.
The rules differ on purpose. Scope is applied before retrieval filtering, scores and counts.
"""

from dataclasses import dataclass

from sqlalchemy import exists, or_, select, true
from sqlalchemy.orm import Session

from app.infrastructure.access import Caller
from app.infrastructure.database.metadata import mission_grants, provider_items


def cube_readable(caller: Caller, mission, silo):
    """The cube rule, for a mission and silo given as columns or values."""
    return exists().where(
        mission_grants.c.user_id == caller.user_id,
        mission_grants.c.mission_key == mission,
        or_(mission_grants.c.silo.is_(None), mission_grants.c.silo == silo),
    )


def mission_listed(caller: Caller, mission):
    """The mission listing, for a mission given as a column or a value."""
    return exists().where(
        mission_grants.c.user_id == caller.user_id, mission_grants.c.mission_key == mission
    )


def document_readable(caller: Caller):
    """The document rule over provider_items."""
    if "admin" in caller.roles:
        return true()
    return or_(
        provider_items.c.mission_key.is_(None),
        cube_readable(caller, provider_items.c.mission_key, provider_items.c.silo),
    )


@dataclass(frozen=True, slots=True)
class DocumentAccess:
    """The document rule for provider records checked in Python, from grants read once."""

    admin: bool
    grants: frozenset[tuple[str, str | None]]

    def readable(self, record) -> bool:
        mission = record["mission_key"]
        return (
            self.admin
            or mission is None
            or (mission, None) in self.grants
            or (mission, record["silo"]) in self.grants
        )


def document_access(session: Session, caller: Caller) -> DocumentAccess:
    if "admin" in caller.roles:
        return DocumentAccess(admin=True, grants=frozenset())
    rows = session.execute(
        select(mission_grants.c.mission_key, mission_grants.c.silo).where(
            mission_grants.c.user_id == caller.user_id
        )
    )
    return DocumentAccess(admin=False, grants=frozenset((mission, silo) for mission, silo in rows))


def insert_grant(session: Session, user, mission: str, silo: str | None) -> None:
    from sqlalchemy.dialects.postgresql import insert

    session.execute(
        insert(mission_grants)
        .values(user_id=user, mission_key=mission, silo=silo)
        .on_conflict_do_nothing()
    )


def remove_grant(session: Session, user, mission: str, silo: str | None) -> None:
    session.execute(
        mission_grants.delete().where(
            mission_grants.c.user_id == user,
            mission_grants.c.mission_key == mission,
            mission_grants.c.silo == silo,
        )
    )
