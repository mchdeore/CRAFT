import pytest

from app.infrastructure.database import metadata as m
from app.tests.support.chat import chat as chat
from app.tests.support.chat import remove_extra_conversations as remove_extra_conversations
from app.tests.support.cube import cube_source as cube_source


@pytest.fixture(autouse=True)
def remove_skills(chat):
    yield
    if "skill_revisions" in m.metadata.tables:
        with chat["owner"].begin() as connection:
            connection.execute(
                m.skill_revisions.delete().where(
                    m.skill_revisions.c.owner == chat["caller"].user_id
                )
            )
            connection.execute(m.skills.delete().where(m.skills.c.owner == chat["caller"].user_id))
