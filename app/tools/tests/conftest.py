import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.tests.support.chat import chat as chat
from app.tests.support.chat import remove_extra_conversations as remove_extra_conversations
from app.tests.support.cube import cube_source as cube_source
from app.tests.support.notes import remove_notes as remove_notes


@pytest.fixture
def chart_http(chat):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        yield http
