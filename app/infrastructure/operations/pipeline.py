"""The shared validation, permission, observation and transport pipeline."""

import asyncio
import inspect
import logging
from functools import wraps
from time import perf_counter
from typing import Annotated, Any, get_args, get_origin
from uuid import UUID, uuid4

import anyio
import msgspec
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from app.infrastructure.access import Caller, anonymous_http, require, resolve_http
from app.infrastructure.errors import ApiError, InvalidInput, TooLarge, error_response
from app.infrastructure.operations.context import (
    CallContext,
    current_call,
    response_actions,
    transport_request,
)
from app.infrastructure.operations.registry import Operation, registry, stream_output
from app.infrastructure.operations.service import record
from app.infrastructure.requests import current_request_id
from app.infrastructure.shapes import FieldError, Health, RawFile, json_response


class ApprovalRequired(ApiError):
    status, code, message = 409, "approval_required", "Approval required."


class OperationStreamResponse(StreamingResponse):
    """Close the primed source even when response start precedes a disconnect."""

    def __init__(self, *args, source, **kwargs):
        super().__init__(*args, **kwargs)
        self.source = source

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.source.aclose()


def decode(operation: Operation, value: Any) -> Any:
    if operation.input is None:
        if value not in (None, {}):
            raise InvalidInput(fields=[FieldError(path="$", message="No input is accepted.")])
        return None
    try:
        if value is None:
            value = {}
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
        return msgspec.json.decode(
            msgspec.json.encode(value),
            type=stream_output(operation.output) or operation.output,
            strict=True,
        )
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
        "outcome": "error" if error or ctx.error_code else "ok",
        "error_code": error.code if error else ctx.error_code,
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
        "workspace_token_id": caller.workspace_token_id,
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


def checked_access(operation: Operation, caller: Caller) -> Caller:
    http = transport_request.get()
    call = current_call.get()
    if (
        caller.origin == "http"
        and http is not None
        and not operation.name.startswith("health.")
        and (call is None or call.parent_call_id is None)
    ):
        caller = resolve_http(http)
        http.state.caller = caller  # read by the traffic middleware once the response ends
        context = current_call.get()
        if context is not None:
            context.caller = caller
    require(caller, operation.access)
    # The token gates the route it calls; that operation's own nested reads are not routes.
    top_level = call is None or call.parent_call_id is None
    personal = caller.origin == "workspace" or caller.workspace_token_id is not None
    if personal and top_level and not token_allowed(operation, caller.origin):
        from app.infrastructure.errors import Forbidden

        raise Forbidden()
    # A browser never attaches a Bearer or Hub token by itself (unlike cached Basic credentials),
    # and can't send one cross-site without CORS allowing it, so those requests skip the check.
    if (
        caller.origin == "http"
        and http is not None
        and http.method in {"POST", "PUT", "PATCH", "DELETE"}
        and not http.headers.get("authorization", "").startswith(("Bearer ", "token "))
    ):
        if caller.settings is None or http.headers.get("origin") not in {
            o.strip() for o in caller.settings.allowed_origins.split(",")
        }:
            from app.infrastructure.errors import Forbidden

            raise Forbidden()
    return caller


def token_allowed(operation: Operation, origin: str) -> bool:
    """Personal tokens reach the agent's read-tool catalog; a workspace token also the account
    check, an MCP token also ask_agent."""
    return operation.name == ("accounts.me" if origin == "workspace" else "agent.ask_agent") or (
        operation.tool and operation.effects == "NONE" and operation.tool_name != "ask_agent"
    )


def checked_input(operation: Operation, caller: Caller, value: Any) -> Any:
    if raw_file(operation):
        content = (
            value.get("content") if isinstance(value, dict) else getattr(value, "content", None)
        )
        if isinstance(content, (bytes, bytearray)) and len(content) > RawFile.max_bytes:
            raise TooLarge()
    request = decode(operation, value)
    # A granted call is checked against its notebook grant when it is claimed instead.
    if caller.origin == "mcp" and operation.effects != "NONE" and operation.granted is None:
        from app.approvals.service import require_approved

        require_approved(caller, operation, request)
    return caller, request


def checked(operation: Operation, caller: Caller, value: Any) -> Any:
    return checked_input(operation, checked_access(operation, caller), value)


class RawHttpFile:
    def __init__(self, request: Request, limit: int):
        self.request = request
        self.limit = limit

    async def read(self):
        content = bytearray()
        async for chunk in self.request.stream():
            if len(content) + len(chunk) > self.limit:
                raise TooLarge()
            content.extend(chunk)
        simulated = self.request.headers.get("x-simulated", "false").lower()
        if simulated not in {"true", "false"}:
            raise InvalidInput()
        return (
            dict(self.request.query_params)
            | dict(self.request.path_params)
            | {
                "content": bytes(content),
                "fileName": self.request.headers.get("x-file-name", ""),
                "mediaType": self.request.headers.get("content-type", "").split(";", 1)[0].lower(),
                "simulated": simulated == "true",
            }
        )


class JsonHttpInput:
    def __init__(self, request: Request, limit: int):
        self.request, self.limit = request, limit

    async def read(self):
        content = bytearray()
        async for chunk in self.request.stream():
            if len(content) + len(chunk) > self.limit:
                raise TooLarge()
            content.extend(chunk)
        try:
            value = msgspec.json.decode(content)
        except msgspec.DecodeError:
            return bytes(content)
        if isinstance(value, dict):
            return dict(self.request.query_params) | value | dict(self.request.path_params)
        return bytes(content)


def raw_file(operation: Operation) -> bool:
    return operation.input is not None and issubclass(operation.input, RawFile)


def wrap(operation: Operation) -> Any:
    fn = operation.function
    if inspect.isasyncgenfunction(fn):

        @wraps(fn)
        async def streaming(caller: Caller, request: Any = None):
            ctx, token = begin(caller)
            current_call.reset(token)
            error = None
            iterator = None
            try:
                token = current_call.set(ctx)
                try:
                    resolved = checked_access(operation, caller)
                    value = await request.read() if isinstance(request, JsonHttpInput) else request
                    resolved, value = checked_input(operation, resolved, value)
                    iterator = fn(resolved, value) if operation.input else fn(resolved)
                finally:
                    current_call.reset(token)
                while True:
                    token = current_call.set(ctx)
                    try:
                        try:
                            value = await anext(iterator)
                        except StopAsyncIteration:
                            break
                        output = validate_output(operation, value)
                    finally:
                        current_call.reset(token)
                    yield output
            except (asyncio.CancelledError, GeneratorExit):
                error = ApiError()
                raise
            except ApiError as failure:
                error = failure
                raise
            except Exception:
                error = ApiError()
                raise error from None
            finally:
                with anyio.CancelScope(shield=True):
                    token = current_call.set(ctx)
                    try:
                        if iterator is not None:
                            await iterator.aclose()
                    finally:
                        try:
                            await run_in_threadpool(
                                finish, ctx, None, operation.name, "operation", error
                            )
                        finally:
                            current_call.reset(token)

        wrapper = streaming
    elif (
        inspect.iscoroutinefunction(fn)
        or raw_file(operation)
        or (operation.input and getattr(operation.input, "max_bytes", None))
    ):

        @wraps(fn)
        async def asynchronous(caller: Caller, request: Any = None) -> Any:
            ctx, token = begin(caller)
            error = None
            output = None
            try:
                if isinstance(request, (RawHttpFile, JsonHttpInput)):
                    resolved = checked_access(operation, caller)
                    resolved, value = checked_input(operation, resolved, await request.read())
                else:
                    resolved, value = checked(operation, caller, request)
                if resolved.origin == "mcp" and operation.effects == "EXTERNAL":
                    from app.approvals.service import claim_external_approved, claim_granted

                    claim = claim_granted if operation.granted else claim_external_approved
                    await run_in_threadpool(claim, resolved, operation, value)
                    output = await fn(resolved, value) if operation.input else await fn(resolved)
                elif resolved.origin == "mcp" and operation.effects != "NONE":
                    from app.approvals.service import execute_approved

                    output = await run_in_threadpool(
                        execute_approved,
                        resolved,
                        operation,
                        value,
                        lambda: validate_output(
                            operation, fn(resolved, value) if operation.input else fn(resolved)
                        ),
                    )
                elif inspect.iscoroutinefunction(fn):
                    output = await fn(resolved, value) if operation.input else await fn(resolved)
                else:
                    output = await run_in_threadpool(fn, resolved, value)
                return validate_output(operation, output)
            except asyncio.CancelledError:
                error = ApiError()
                raise
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
                if resolved.origin == "mcp" and operation.effects != "NONE":
                    from app.approvals.service import execute_approved

                    return execute_approved(
                        resolved,
                        operation,
                        value,
                        lambda: validate_output(
                            operation, fn(resolved, value) if operation.input else fn(resolved)
                        ),
                    )
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
        request.state.operation = operation.name  # read by the traffic middleware
        caller = anonymous_http(request)
        token = transport_request.set(request)
        action_token = response_actions.set([])
        try:
            values: Any = dict(request.query_params)
            values.update(request.path_params)
            if operation.name.startswith("health."):
                values = {}
            if raw_file(operation):
                assert operation.input is not None
                values = RawHttpFile(request, RawFile.max_bytes)
            elif operation.input and getattr(operation.input, "max_bytes", None):
                values = JsonHttpInput(request, getattr(operation.input, "max_bytes", 0))
            elif operation.input:
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
            if inspect.isasyncgenfunction(wrapper):
                iterator = wrapper(caller, values or None)
                try:
                    first = await anext(iterator)
                except StopAsyncIteration:
                    first = None

                async def encoded():
                    try:
                        if first is not None:
                            yield b"data: " + msgspec.json.encode(first) + b"\n\n"
                        async for event in iterator:
                            yield b"data: " + msgspec.json.encode(event) + b"\n\n"
                    finally:
                        with anyio.CancelScope(shield=True):
                            await iterator.aclose()

                result = OperationStreamResponse(
                    encoded(),
                    source=iterator,
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
                )
            elif inspect.iscoroutinefunction(wrapper):
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
        except ApiError as error:
            response = error_response(error)
        finally:
            transport_request.reset(token)
            response_actions.reset(action_token)
        if not operation.name.startswith("health."):
            response.headers["Cache-Control"] = "no-store"
        return response

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
