"""Workspace bytes, labels, limits and safe download logs use owned records."""

from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import NotFound
from app.infrastructure.shapes import RawFile


def upload(http, chat, content=b"Synthetic bytes", name="made-up.txt", extra=""):
    return http.post(
        "/api/conversations/" + str(chat["thread"]) + "/files" + extra,
        content=content,
        headers={
            "Origin": "https://testserver",
            "Content-Type": "text/plain",
            "X-File-Name": name,
            "X-Simulated": "true",
        },
    )


def test_workspace_owner_content_and_state(chat, foreign_http, monkeypatch):
    from uuid import UUID

    from app.conversations import storage
    from app.conversations.handlers import FileRef, file_content

    original = storage.file_state
    observed = []

    def inspect_state(session, identifier, state):
        with chat["owner"].connect() as connection:
            observed.append(
                connection.execute(
                    select(m.workspace_files.c.state).where(m.workspace_files.c.id == identifier)
                ).scalar_one()
            )
        return original(session, identifier, state)

    monkeypatch.setattr(storage, "file_state", inspect_state)
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        result = upload(http, chat)
        assert result.status_code == 200 and result.json()["state"] == "ready"
        identifier = result.json()["id"]
        assert observed[:2] == ["uploading", "processing"]
        assert (
            foreign_http.get(
                f"/api/conversations/{chat['thread']}/files/{identifier}/content"
            ).status_code
            == 404
        )
        assert (
            foreign_http.delete(
                f"/api/conversations/{chat['thread']}/files/{identifier}",
                headers={"Origin": "https://testserver"},
            ).status_code
            == 404
        )

        response = http.get(f"/api/conversations/{chat['thread']}/files/{identifier}/content")
        equal = response.content == b"Synthetic bytes"
        assert response.status_code == 200 and equal
        cache_policy = response.headers.get("Cache-Control")
        assert cache_policy == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Content-Disposition"].startswith("attachment;")
        assert response.headers["Content-Type"].startswith("text/plain")
        assert (
            http.delete(
                f"/api/conversations/{chat['thread']}/files/{identifier}",
                headers={"Origin": "https://testserver"},
            ).status_code
            == 204
        )
        refused = http.get(f"/api/conversations/{chat['thread']}/files/{identifier}/content")
        assert refused.status_code == 404
        cache_policy = refused.headers.get("Cache-Control")
        assert cache_policy == "no-store"
        identifier = upload(http, chat).json()["id"]
    with pytest.raises(NotFound):
        file_content(
            replace(chat["caller"], user_id=uuid4()),
            FileRef(id=chat["thread"], file_id=UUID(identifier)),
        )


@pytest.mark.slow
def test_workspace_limits_archive_and_labels(chat):
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        assert upload(http, chat, b"x" * (RawFile.max_bytes + 1)).status_code == 413
        assert upload(http, chat, name="../../outside.txt").status_code == 400
        result = upload(http, chat, extra="?classification=protected-b")
        assert (
            result.json()["simulated"] is True and result.json()["classification"] == "protected-b"
        )
        for _ in range(49):
            assert upload(http, chat).status_code == 200
        assert upload(http, chat).status_code == 409
        assert (
            http.patch(
                "/api/conversations/" + str(chat["thread"]),
                json={"archived": True},
                headers={"Origin": "https://testserver"},
            ).status_code
            == 200
        )
        assert http.get("/api/conversations/" + str(chat["thread"]) + "/files").status_code == 404
    with chat["owner"].connect() as connection:
        assert (
            connection.execute(
                select(m.workspace_files.c.id).where(
                    m.workspace_files.c.conversation_id == chat["thread"]
                )
            ).first()
            is None
        )


def test_workspace_download_logged_without_bytes(chat, capsys):
    marker = uuid4().hex
    with TestClient(create_app(chat["settings"]), base_url="https://testserver") as http:
        assert chat["sign_in"](http).status_code == 200
        result = upload(http, chat, marker.encode(), marker + ".txt")
        assert result.status_code == 200
        identifier = result.json()["id"]
        assert (
            http.get(f"/api/conversations/{chat['thread']}/files/{identifier}/content").status_code
            == 200
        )
    captured = capsys.readouterr()
    logs = m.metadata.tables["call_log"]
    with chat["owner"].connect() as connection:
        rows = (
            connection.execute(
                select(logs).where(
                    logs.c.name == "conversations.file_content",
                    logs.c.conversation_id == chat["thread"],
                )
            )
            .mappings()
            .all()
        )
    safe = marker not in captured.out + captured.err and all(
        marker not in str(v) for r in rows for v in r.values()
    )
    assert (
        safe
        and len(rows) == 1
        and rows[0]["user_id"] == chat["caller"].user_id
        and rows[0]["auth_session_id"]
    )
