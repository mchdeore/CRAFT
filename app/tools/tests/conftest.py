import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.tests.support.chat import chat as chat
from app.tests.support.cube import cube_source as cube_source


@pytest.fixture
def chart_http(chat):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        yield http
