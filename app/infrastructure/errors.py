"""ApiError and its subclasses. Every non-2xx body is {code, message, requestId, fields?}."""

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from starlette.responses import Response

from app.infrastructure.requests import current_request_id
from app.infrastructure.shapes import ApiErrorBody, FieldError, json_response


class ApiError(Exception):
    status, code, message = 500, "internal_error", "Internal error."

    def __init__(self, message: str | None = None, fields: list[FieldError] | None = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.fields = fields


class InvalidInput(ApiError):
    status, code, message = 400, "invalid_input", "Invalid input."


class NotSignedIn(ApiError):
    status, code, message = 401, "not_signed_in", "Not signed in."


class Forbidden(ApiError):
    status, code, message = 403, "forbidden", "Forbidden."


class NotFound(ApiError):
    status, code, message = 404, "not_found", "Not found."


class Conflict(ApiError):
    status, code, message = 409, "conflict", "Conflict."


class RateLimited(ApiError):
    status, code, message = 429, "rate_limited", "Too many requests."


class TooLarge(ApiError):
    status, code, message = 413, "too_large", "The file is too large."


def error_response(error: ApiError) -> Response:
    body = ApiErrorBody(
        code=error.code, message=error.message, request_id=current_request_id(), fields=error.fields
    )
    return json_response(body, error.status)


def install_error_handlers(app: FastAPI) -> None:
    """ApiError → its body. Unknown routes and methods → 404 not_found; nothing else revealed."""

    async def on_api_error(_: Request, error: Exception) -> Response:
        assert isinstance(error, ApiError)
        return error_response(error)

    async def on_http_error(_: Request, error: Exception) -> Response:
        assert isinstance(error, HTTPException)
        return error_response(NotFound() if error.status_code in (404, 405) else ApiError())

    app.add_exception_handler(ApiError, on_api_error)
    app.add_exception_handler(HTTPException, on_http_error)
