import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.conversations.tests.conftest import (
    remove_extra_conversations as remove_extra_conversations,
)
from app.cube.tests.conftest import cube_source as cube_source
from app.notes.tests.conftest import chat as chat
from app.notes.tests.conftest import remove_notes as remove_notes


@pytest.fixture
def chart_http(chat):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        yield http
