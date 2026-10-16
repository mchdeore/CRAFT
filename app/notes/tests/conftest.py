import pytest

from app.agent.tests.test_chat_stream import chat as chat
from app.conversations.tests.conftest import (
    remove_extra_conversations as remove_extra_conversations,
)
from app.cube.tests.conftest import cube_source as cube_source
from app.infrastructure.database import metadata as m


@pytest.fixture(autouse=True)
def remove_notes(chat, remove_extra_conversations):
    yield
    with chat["owner"].begin() as connection:
        connection.execute(m.notes.delete().where(m.notes.c.owner == chat["caller"].user_id))
