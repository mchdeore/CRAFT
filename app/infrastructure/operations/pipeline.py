"""The shared validation, permission, observation and transport pipeline."""

import inspect
import logging
from functools import wraps
from time import perf_counter
from typing import Annotated, Any, get_args, get_origin
from uuid import UUID, uuid4

import msgspec
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response

from app.infrastructure.access import Caller, anonymous_http, require, resolve_http
from app.infrastructure.errors import ApiError, InvalidInput, error_response
from app.infrastructure.operations.context import (
    CallContext,
    current_call,
    response_actions,
    transport_request,
)
from app.infrastructure.operations.registry import Operation, registry
from app.infrastructure.operations.service import record
from app.infrastructure.requests import current_request_id
from app.infrastructure.shapes import FieldError, Health, json_response


class ApprovalRequired(ApiError):
    status, code, message = 409, "approval_required", "Approval required."


def decode(operation: Operation, value: Any) -> Any:
    if operation.input is None:
        if value not in (None, {}):
            raise InvalidInput(fields=[FieldError(path="$", message="No input is accepted.")])
        return None
    try:
        if isinstance(value, operation.input):
            return msgspec.json.decode(msgspec.json.encode(value), type=operation.input)
        if isinstance(value, (bytes, bytearray)):
            return msgspec.json.decode(value, type=operation.input)
        return msgspec.convert(value, operation.input, strict=True)
    except (msgspec.ValidationError, msgspec.DecodeError, TypeError, ValueError):
        raise InvalidInput(
            fields=[FieldError(path="$", message="Input does not match the declared fields.")]
        ) from None


def validate_output(operation: Operation, value: Any) -> Any:
    if operation.output is Response and isinstance(value, Response):
        return value
    if operation.output is type(None):
        if value is not None:
            raise ApiError()
        return None
    try:
        return msgspec.convert(value, operation.output, strict=True)
    except (msgspec.ValidationError, TypeError, ValueError):
        raise ApiError() from None


def begin(caller: Caller) -> tuple[CallContext, Any]:
    parent = current_call.get()
    rid = (
        parent.request_id
        if parent
        else UUID(current_request_id())
        if current_request_id()
        else uuid4()
    )
    ctx = CallContext(rid, uuid4(), parent.call_id if parent else None, caller, perf_counter())
    return ctx, current_call.set(ctx)


def finish(
    ctx: CallContext,
    token: Any,
    name: str,
    kind: str,
    error: ApiError | None,
    skip_database: bool = False,
) -> None:
    caller = ctx.caller
    fields = {
        "kind": kind,
        "name": name,
        "origin": caller.origin,
        "outcome": "error" if error else "ok",
        "error_code": error.code if error else None,
        "duration_ms": round((perf_counter() - ctx.started) * 1000),
        "db_statements": ctx.db_statements,
        "db_ms": round(ctx.db_ms),
        "request_id": ctx.request_id,
        "call_id": ctx.call_id,
        "parent_call_id": ctx.parent_call_id,
        "user_id": caller.user_id,
        "auth_session_id": caller.auth_session_id,
        "conversation_id": caller.conversation_id,
        "run_id": caller.run_id,
        "tool_call_id": caller.tool_call_id,
    }
    # Log persistence is not included in domain statement counts or the parent call.
    local_token = current_call.set(None)
    try:
        logging.getLogger("craft.operations").info(kind, extra={"fields": fields})
        if not skip_database:
            record(caller, fields)
    finally:
        current_call.reset(local_token)
        if token is not None:
            current_call.reset(token)


def checked(operation: Operation, caller: Caller, value: Any) -> Any:
    http = transport_request.get()
    call = current_call.get()
    if (
        caller.origin == "http"
        and http is not None
        and not operation.name.startswith("health.")
        and (call is None or call.parent_call_id is None)
    ):
        caller = resolve_http(http)
        context = current_call.get()
        if context is not None:
            context.caller = caller
    require(caller, operation.access)
    if (
        caller.origin == "http"
        and http is not None
        and http.method in {"POST", "PUT", "PATCH", "DELETE"}
    ):
        if caller.settings is None or http.headers.get("origin") not in {
            o.strip() for o in caller.settings.allowed_origins.split(",")
        }:
            from app.infrastructure.errors import Forbidden

            raise Forbidden()
    request = decode(operation, value)
    if caller.origin == "mcp" and operation.effects != "NONE":
        raise ApprovalRequired()
    return caller, request


def wrap(operation: Operation) -> Any:
    fn = operation.function
    if inspect.iscoroutinefunction(fn):

        @wraps(fn)
        async def asynchronous(caller: Caller, request: Any = None) -> Any:
            ctx, token = begin(caller)
            error = None
            output = None
            try:
                resolved, value = checked(operation, caller, request)
                output = await fn(resolved, value) if operation.input else await fn(resolved)
                return validate_output(operation, output)
            except ApiError as failure:
                error = failure
                raise
            except Exception:
                error = ApiError()
                raise error from None
            finally:
                skip = operation.name == "health.live" or (
                    isinstance(output, Health) and output.status == "unavailable"
                )
                try:
                    await run_in_threadpool(
                        finish, ctx, None, operation.name, "operation", error, skip
                    )
                finally:
                    current_call.reset(token)

        wrapper = asynchronous
    else:

        @wraps(fn)
        def synchronous(caller: Caller, request: Any = None) -> Any:
            ctx, token = begin(caller)
            error = None
            try:
                resolved, value = checked(operation, caller, request)
                result = fn(resolved, value) if operation.input else fn(resolved)
                return validate_output(operation, result)
            except ApiError as failure:
                error = failure
                raise
            except Exception:
                error = ApiError()
                raise error from None
            finally:
                finish(
                    ctx, token, operation.name, "operation", error, operation.name == "health.live"
                )

        wrapper = synchronous
    wrapper.__dict__["__operation__"] = operation
    return wrapper


def query_base_type(shape: Any) -> Any:
    if get_origin(shape) is Annotated:
        return query_base_type(get_args(shape)[0])
    parts = [part for part in get_args(shape) if part is not type(None)]
    return query_base_type(parts[0]) if len(parts) == 1 else shape


def endpoint(operation: Operation) -> Any:
    wrapper = operation.function

    async def handle(request: Request) -> Response:
        caller = anonymous_http(request)
        token = transport_request.set(request)
        action_token = response_actions.set([])
        try:
            values: Any = dict(request.query_params)
            values.update(request.path_params)
            if operation.name.startswith("health."):
                values = {}
            if operation.input:
                # Only declared non-string query fields are parsed. JSON bodies remain strict.
                for field in msgspec.structs.fields(operation.input):
                    name = field.encode_name
                    if (
                        name in values
                        and name not in request.path_params
                        and query_base_type(field.type) in (int, float, bool)
                    ):
                        try:
                            values[name] = msgspec.json.decode(values[name].encode())
                        except msgspec.DecodeError:
                            pass
                body = await request.body()
                if body:
                    try:
                        decoded = msgspec.json.decode(body)
                        if not isinstance(decoded, dict):
                            raise ValueError
                        values.update(decoded)
                        values.update(request.path_params)
                    except (msgspec.DecodeError, ValueError):
                        values = body
            elif await request.body():
                values = await request.body()
            if inspect.iscoroutinefunction(wrapper):
                result = await wrapper(caller, values or None)
            else:
                result = await run_in_threadpool(wrapper, caller, values or None)
            if result is None:
                response = Response(status_code=204)
            elif isinstance(result, Response):
                response = result
            else:
                status = (
                    503 if isinstance(result, Health) and result.status == "unavailable" else 200
                )
                response = json_response(result, status)
            for action in response_actions.get() or []:
                response.set_cookie(**action)
            return response
        except ApiError as error:
            return error_response(error)
        finally:
            transport_request.reset(token)
            response_actions.reset(action_token)

    handle.__dict__["__operation__"] = operation
    return handle


def register_http(app: FastAPI) -> None:
    for operation in registry.values():
        if operation.path:
            assert operation.method is not None
            path = (
                operation.path if operation.name.startswith("health.") else "/api" + operation.path
            )
            app.add_api_route(
                path, endpoint(operation), methods=[operation.method], response_model=None
            )
