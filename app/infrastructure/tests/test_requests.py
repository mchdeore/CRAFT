import io
import json
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from app.infrastructure.logging import configure_logging
from app.infrastructure.requests import current_request_id


def boom() -> None:
    raise RuntimeError("database password is hunter2")


def which_request() -> JSONResponse:  # sync: runs in FastAPI's thread pool
    return JSONResponse({"requestId": current_request_id()})


def test_request_id_on_every_response(app: FastAPI) -> None:
    app.add_api_route("/boom", boom, methods=["GET"])
    with TestClient(app) as client:
        ok = client.get("/health/live")
        missing = client.get("/nope")
        wrong_method = client.post("/health/live")
        crashed = client.get("/boom")
    assert ok.status_code == 200 and uuid.UUID(ok.headers["x-request-id"])
    for response, status, code in (
        (missing, 404, "not_found"),
        (wrong_method, 404, "not_found"),
        (crashed, 500, "internal_error"),
    ):
        body = response.json()
        assert response.status_code == status and body["code"] == code
        assert body["requestId"] == response.headers["x-request-id"]
    assert "hunter2" not in crashed.text


def test_request_id_reaches_thread_pool(app: FastAPI) -> None:
    app.add_api_route("/which", which_request, methods=["GET"])
    with TestClient(app) as client:
        response = client.get("/which")
    assert response.json()["requestId"] == response.headers["x-request-id"]


def test_log_line_is_json_without_content(app: FastAPI) -> None:
    app.add_api_route("/boom", boom, methods=["POST"])
    stream = io.StringIO()
    configure_logging("INFO", stream)
    with TestClient(app) as client:
        client.get("/health/live?token=SECRET-QUERY")
        client.post("/boom?token=SECRET-QUERY", content=b'{"password": "SECRET-BODY"}')
    output = stream.getvalue()
    lines = [json.loads(line) for line in output.splitlines()]
    http = [line for line in lines if line["event"] == "http"]
    assert [(line["path"], line["status"]) for line in http] == [
        ("/health/live", 200),
        ("/boom", 500),
    ]
    assert set(http[0]) == {"at", "level", "logger", "event", "method", "path", "status",
                            "duration_ms", "request_id"}  # fmt: skip
    assert "SECRET" not in output and "hunter2" not in output


def test_mounted_routes_log_their_mount_path(app: FastAPI) -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream)
    with TestClient(app) as client:
        client.post("/mcp", json={})
        client.post("/mcp/", json={})
        client.get("/summarize")
        client.get("/summarize-not-a-mount")
    http = [json.loads(line) for line in stream.getvalue().splitlines() if '"event":"http"' in line]
    assert [(line["path"], line["status"]) for line in http] == [
        ("/mcp", 401),
        ("/mcp", 401),
        ("/summarize", 200),
        ("/unmatched", 404),
    ]
