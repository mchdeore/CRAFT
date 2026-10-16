import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.infrastructure.errors import (
    ApiError,
    Conflict,
    Forbidden,
    InvalidInput,
    NotFound,
    NotSignedIn,
    RateLimited,
)
from app.infrastructure.shapes import FieldError


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidInput, 400, "invalid_input"),
        (NotSignedIn, 401, "not_signed_in"),
        (Forbidden, 403, "forbidden"),
        (NotFound, 404, "not_found"),
        (Conflict, 409, "conflict"),
        (RateLimited, 429, "rate_limited"),
        (ApiError, 500, "internal_error"),
    ],
)
def test_each_error_has_its_status_and_code(
    app: FastAPI, error: type[ApiError], status: int, code: str
) -> None:
    def fail() -> None:
        raise error()

    app.add_api_route("/fail", fail, methods=["GET"])
    with TestClient(app) as client:
        response = client.get("/fail")
    assert response.status_code == status
    assert response.json() == {
        "code": code,
        "message": error.message,
        "requestId": response.headers["x-request-id"],
    }


def test_invalid_input_lists_fields(app: FastAPI) -> None:
    def fail() -> None:
        raise InvalidInput(fields=[FieldError(path="title", message="Must not be empty.")])

    app.add_api_route("/fail", fail, methods=["POST"])
    with TestClient(app) as client:
        body = client.post("/fail").json()
    assert body["fields"] == [{"path": "title", "message": "Must not be empty."}]
