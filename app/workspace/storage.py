"""The one row the workspace reads: the person's identity-provider account."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.database.metadata import metadata


def identity(session: Session, user_id: UUID) -> tuple[str, str] | None:
    """(tenant, object ID) of the linked identity-provider account, or None."""
    users = metadata.tables["users"]
    row = session.execute(
        select(users.c.idp_tenant_id, users.c.idp_object_id).where(users.c.id == user_id)
    ).first()
    if row is None or row[0] is None or row[1] is None:
        return None
    return str(row[0]), str(row[1])
