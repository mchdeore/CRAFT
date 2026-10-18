"""Cleanup for notes created by a synthetic account."""

import pytest

from app.infrastructure.database import metadata as m


@pytest.fixture(autouse=True)
def remove_notes(chat, remove_extra_conversations):
    yield
    with chat["owner"].begin() as connection:
        connection.execute(m.notes.delete().where(m.notes.c.owner == chat["caller"].user_id))
