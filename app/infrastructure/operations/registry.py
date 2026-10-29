"""Validated declarations are the sole source of application routes and tools."""

import inspect
import re
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Any, get_args, get_origin, get_type_hints

from starlette.responses import Response

from app.infrastructure.access import Caller
from app.infrastructure.shapes import Shape


@dataclass(frozen=True)
class Job:
    """Run the operation on a worker queue: calling it queues a job and returns its ID.

    lock maps the input to a key: a second call while one is queued or running returns that
    job. interrupted(sessions, input) runs when a worker died and the job will not be retried.
    every is a cron string: the workers queue the job once per tick, as the system (a PUBLIC
    operation with no input), and a tick whose previous run is still going is skipped.
    notify tells the person who queued the job when it finishes or finally fails.
    """

    queue: str
    retry: bool = True
    timeout: float | None = None
    lock: Callable[[Any], str] | None = None
    interrupted: Callable[[Any, Any], None] | None = None
    every: str | None = None
    notify: bool = False


@dataclass(frozen=True)
class Operation:
    name: str
    function: Any
    access: str
    method: str | None
    path: str | None
    tool: bool
    effects: str
    input: type[Shape] | None
    output: Any
    prepare_approved: Callable[[Caller, Any], object] | None = None
    job: Job | None = None
    # The checked function a worker runs; the declared function queues it.
    run: Any = None
    # Notebook grants: approving a `grants` call grants the notebook its argument names, for
    # that conversation; a `granted` call on a granted notebook then runs without asking.
    grants: str | None = None
    granted: str | None = None

    @property
    def tool_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]


registry: dict[str, Operation] = {}


def tool_operations(operations: Mapping[str, Operation], *, agent: bool = False) -> list[Operation]:
    return [
        operation
        for operation in operations.values()
        if operation.tool
        and (not agent or (operation.effects == "NONE" and operation.tool_name != "ask_agent"))
    ]


def tool_named(operations: Mapping[str, Operation], name: str) -> Operation | None:
    return next(
        (operation for operation in tool_operations(operations) if operation.tool_name == name),
        None,
    )


def stream_output(shape: Any) -> Any:
    return get_args(shape)[0] if get_origin(shape) in (AsyncIterator, AsyncGenerator) else None


def is_output(shape: Any) -> bool:
    return (
        shape is type(None)
        or shape is Response
        or (inspect.isclass(shape) and issubclass(shape, Shape))
        or (stream_output(shape) is not None and is_output(stream_output(shape)))
        or (
            get_origin(shape) is list
            and len(get_args(shape)) == 1
            and is_output(get_args(shape)[0])
        )
    )


def declare(
    function: Any,
    access: str,
    routes: dict[str, str | None],
    tool: bool,
    effects: str,
    prepare_approved: Callable[[Caller, Any], object] | None = None,
    job: Job | None = None,
) -> Operation:
    if getattr(function, "__observed__", False):
        raise ValueError("Observation cannot decorate an operation")
    if not access or effects not in {"NONE", "DATA", "DOCUMENT", "EXTERNAL", "FINAL"}:
        raise ValueError("Invalid operation declaration")
    if prepare_approved is not None and (
        effects not in {"DATA", "DOCUMENT"}
        or not callable(prepare_approved)
        or inspect.iscoroutinefunction(prepare_approved)
        or inspect.isasyncgenfunction(prepare_approved)
    ):
        raise ValueError("Approved preparation requires a synchronous database effect")
    hints = get_type_hints(function)
    params = list(inspect.signature(function).parameters.values())
    if not params or params[0].name != "caller" or hints.get("caller") is not Caller:
        raise ValueError("Operation requires a trusted caller")
    if len(params) > 2:
        raise ValueError("Operation takes one input shape")
    input_shape = hints.get(params[1].name) if len(params) == 2 else None
    if input_shape is not None and (
        not inspect.isclass(input_shape) or not issubclass(input_shape, Shape)
    ):
        raise ValueError("Operation input must be a Shape")
    if len(params) == 2 and input_shape is None:
        raise ValueError("Operation input must be declared")
    if "return" not in hints or not is_output(hints["return"]):
        raise ValueError("Operation output must be a Shape")
    if stream_output(hints["return"]) is not None:
        if not inspect.isasyncgenfunction(function) or tool:
            raise ValueError("A stream must be an async generator and cannot be a tool")
    elif inspect.isasyncgenfunction(function):
        raise ValueError("A stream requires a declared event Shape")
    selected = [(verb, path) for verb, path in routes.items() if path is not None]
    if len(selected) > 1:
        raise ValueError("Operation has more than one route")
    method, path = selected[0] if selected else (None, None)
    if tool and (
        not path
        or not inspect.getdoc(function)
        or not re.fullmatch(r"[a-z0-9_]{1,64}", function.__name__)
    ):
        raise ValueError("Tool requires a route, description and valid name")
    parts = function.__module__.split(".")
    feature = parts[1] if len(parts) > 1 and parts[0] == "app" else "probe"
    name = f"{feature}.{function.__name__}"
    if name in registry or any(
        o.path == path and o.method == method for o in registry.values() if path
    ):
        raise ValueError("Duplicate operation name or route")
    if tool and any(o.tool and o.tool_name == function.__name__ for o in registry.values()):
        raise ValueError("Duplicate tool name")
    if job is not None and (
        tool
        or effects != "NONE"
        or hints["return"] is not type(None)
        or inspect.isasyncgenfunction(function)
    ):
        raise ValueError("A job returns nothing, needs no approval and is not a tool")
    if job is not None and job.every is not None:
        from croniter import croniter

        from app.infrastructure.access import PUBLIC

        if access != PUBLIC or input_shape is not None or path or job.notify:
            raise ValueError("A scheduled job runs as the system: public, no input, no route")
        if not croniter.is_valid(job.every):
            raise ValueError("A scheduled job needs a valid cron string")
    return Operation(
        name,
        function,
        access,
        method,
        path,
        tool,
        effects,
        input_shape,
        hints["return"],
        prepare_approved,
        job,
    )


def validate_routes(app: Any) -> None:
    for route in app.routes:
        if getattr(route, "path", None) in {"/mcp", "/dev", "/summarize"}:
            continue
        if getattr(getattr(route, "endpoint", None), "__operation__", None) is None:
            raise ValueError("Every route must be an operation")
