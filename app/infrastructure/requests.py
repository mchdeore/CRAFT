"""Pure ASGI middleware, outermost: a new request ID per request, one log line per request,
and a last-resort 500 that still carries the ID."""

from contextvars import ContextVar
from time import perf_counter
from typing import Any
from uuid import uuid4

import msgspec
from starlette.routing import Mount

from app.infrastructure.logging import get_logger

log = get_logger("craft.http")
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

Scope = dict[str, Any]
Message = dict[str, Any]


def current_request_id() -> str | None:
    """The ID of the request being served; follows into FastAPI's thread pool."""
    return request_id.get()


class RequestIdMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid, started, status, response_started = str(uuid4()), perf_counter(), 500, False
        token = request_id.set(rid)  # never trusts an incoming header

        async def send_with_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status, response_started = message["status"], True
                message["headers"] = [*message.get("headers", []), (b"x-request-id", rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as error:  # last resort: 500 with an ID, no details
            log.error("unhandled", extra={"fields": {"error_type": type(error).__name__}})
            if not response_started:
                await send_internal_error(send_with_id, rid)
        finally:
            duration_ms = round((perf_counter() - started) * 1000, 1)
            log.info(
                "http",
                extra={
                    "fields": {
                        "method": scope["method"],
                        "path": route_path(scope),
                        "status": status,
                        "duration_ms": duration_ms,
                        "request_id": rid,
                    }
                },
            )
            request_id.reset(token)


def route_path(scope: Scope) -> str:
    """The matched route's template. The router names operations on the scope but not mounts,
    which are matched here by prefix against the app's own routes."""
    route = scope.get("route")
    if route is not None:
        return route.path
    path = scope["path"]
    for mount in getattr(scope.get("app"), "routes", ()):
        if isinstance(mount, Mount) and (path == mount.path or path.startswith(mount.path + "/")):
            return mount.path
    return "/unmatched"


async def send_internal_error(send: Any, rid: str) -> None:
    body = msgspec.json.encode({"code": "internal_error", "message": "Internal error.",
                                "requestId": rid})  # fmt: skip
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": 500, "headers": headers})
    await send({"type": "http.response.body", "body": body})
