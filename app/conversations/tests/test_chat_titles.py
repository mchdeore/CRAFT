"""Conversation title validation precedes durable HTTP mutations."""

import pytest
from fastapi.testclient import TestClient

from app import create_app


@pytest.mark.parametrize("title", [" ", " \t\r\n ", "\u00a0\u2003\u2028"])
@pytest.mark.parametrize("rename", [False, True], ids=["create", "rename"])
def test_whitespace_only_title_is_rejected_without_mutation(chat, title, rename):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        chat["sign_in"](http)
        headers = {"Origin": "https://testserver"}
        path = "/api/conversations/" + str(chat["thread"])
        before = http.get("/api/conversations").json()
        history = http.get(path).json()
        response = (
            http.patch(path, json={"title": title}, headers=headers)
            if rename
            else http.post("/api/conversations", json={"title": title}, headers=headers)
        )
        assert response.status_code == 400
        assert http.get("/api/conversations").json() == before
        assert http.get(path).json() == history


@pytest.mark.parametrize(
    "title", ["A", "x" * 200, "é" * 200, "工程" * 100, "🧀" * 200, "  Report \n ", "<b>Report</b>"]
)
def test_nonblank_title_is_preserved_exactly(chat, title):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        chat["sign_in"](http)
        headers = {"Origin": "https://testserver"}
        created = http.post("/api/conversations", json={"title": title}, headers=headers)
        assert created.status_code == 200
        assert created.json()["title"] == title
        assert http.get("/api/conversations/" + created.json()["id"]).json()["title"] == title
        path = "/api/conversations/" + str(chat["thread"])
        before = http.get(path).json()
        renamed = http.patch(path, json={"title": title}, headers=headers)
        assert renamed.status_code == 200
        after = http.get(path).json()
        assert renamed.json()["title"] == after["title"] == title

        def unchanged(value):
            return {k: v for k, v in value.items() if k not in ("title", "updatedAt")}

        assert unchanged(after) == unchanged(before)


def test_default_conversation_title_remains_valid(chat):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        chat["sign_in"](http)
        created = http.post("/api/conversations", json={}, headers={"Origin": "https://testserver"})
        assert created.status_code == 200
        assert created.json()["title"] == "New conversation"
