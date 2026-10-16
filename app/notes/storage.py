"""Note queries receive the service session and never commit independently."""

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import metadata as m


def serialize(session, owner):
    session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": "notes-account:" + str(owner)},
    )


def by_title(session, owner, title):
    return (
        session.execute(select(m.notes).where(m.notes.c.owner == owner, m.notes.c.title == title))
        .mappings()
        .first()
    )


def count(session, owner):
    return session.execute(
        select(func.count()).select_from(m.notes).where(m.notes.c.owner == owner)
    ).scalar_one()


def write(session, owner, request, existing):
    conversation = (
        request.conversation_id
        if request.conversation_id is not None
        else existing["conversation_id"]
        if existing
        else None
    )
    statement = insert(m.notes).values(
        owner=owner, title=request.title, body=request.body, conversation_id=conversation
    )
    statement = statement.on_conflict_do_update(
        index_elements=[m.notes.c.owner, m.notes.c.title],
        set_={"body": request.body, "conversation_id": conversation, "updated_at": func.now()},
    )
    return session.execute(statement.returning(m.notes)).mappings().one()


def owned(session, owner, identifier):
    return (
        session.execute(select(m.notes).where(m.notes.c.owner == owner, m.notes.c.id == identifier))
        .mappings()
        .first()
    )


def list_owned(session, owner, conversation):
    columns = [c for c in m.notes.c if c.name != "body"]
    query = select(*columns, func.octet_length(m.notes.c.body).label("size_bytes")).where(
        m.notes.c.owner == owner
    )
    if conversation is not None:
        query = query.where(m.notes.c.conversation_id == conversation)
    return (
        session.execute(query.order_by(m.notes.c.updated_at.desc(), m.notes.c.id.desc()).limit(100))
        .mappings()
        .all()
    )


def delete(session, owner, identifier):
    return session.execute(
        m.notes.delete()
        .where(m.notes.c.owner == owner, m.notes.c.id == identifier)
        .returning(m.notes.c.id)
    ).first()
