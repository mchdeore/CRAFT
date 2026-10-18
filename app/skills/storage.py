"""Skill queries receive one service-owned session and never commit."""

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import metadata as m


def serialize(session, owner):
    session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": "skills-account:" + str(owner)},
    )


def head(session, owner, identifier):
    return (
        session.execute(
            select(m.skills)
            .where(m.skills.c.owner == owner, m.skills.c.id == identifier)
            .with_for_update()
        )
        .mappings()
        .first()
    )


def create(session, owner, identifier):
    session.execute(
        insert(m.skills)
        .values(id=identifier, owner=owner, revision=0)
        .on_conflict_do_nothing(index_elements=[m.skills.c.id])
    )


def joined(owner):
    return (
        select(
            m.skills.c.id,
            m.skills.c.created_at,
            *[c for c in m.skill_revisions.c if c.name not in ("skill_id", "owner", "created_at")],
            m.skill_revisions.c.created_at.label("updated_at"),
        )
        .select_from(
            m.skills.join(
                m.skill_revisions,
                (m.skills.c.id == m.skill_revisions.c.skill_id)
                & (m.skills.c.owner == m.skill_revisions.c.owner),
            )
        )
        .where(m.skills.c.owner == owner)
    )


def replay(session, owner, mutation):
    return (
        session.execute(joined(owner).where(m.skill_revisions.c.mutation_id == mutation))
        .mappings()
        .first()
    )


def read(session, owner, identifier, revision=None):
    query = joined(owner).where(m.skills.c.id == identifier)
    query = query.where(
        m.skill_revisions.c.revision == (m.skills.c.revision if revision is None else revision)
    )
    return session.execute(query).mappings().first()


def list_owned(session, owner, after, limit):
    query = joined(owner).where(m.skill_revisions.c.revision == m.skills.c.revision)
    if after is not None:
        query = query.where(m.skills.c.id > after)
    return session.execute(query.order_by(m.skills.c.id).limit(limit + 1)).mappings().all()


def append(session, owner, request, request_hash):
    session.execute(
        m.skill_revisions.insert().values(
            skill_id=request.id,
            owner=owner,
            revision=request.expected_revision + 1,
            mutation_id=request.mutation_id,
            request_hash=request_hash,
            name=request.name,
            description=request.description,
            instructions=request.instructions,
            example_inputs=request.example_inputs,
            tool_names=request.tool_names,
        )
    )


def advance(session, owner, request):
    return session.execute(
        m.skills.update()
        .where(
            m.skills.c.id == request.id,
            m.skills.c.owner == owner,
            m.skills.c.revision == request.expected_revision,
        )
        .values(revision=request.expected_revision + 1)
        .returning(m.skills.c.id)
    ).first()
