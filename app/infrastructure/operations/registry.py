"""Validated declarations are the sole source of application routes and tools."""

import inspect
import re
from dataclasses import dataclass
from typing import Any, get_args, get_origin, get_type_hints

from app.infrastructure.access import Caller
from app.infrastructure.shapes import Shape


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

    @property
    def tool_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]


registry: dict[str, Operation] = {}


def is_output(shape: Any) -> bool:
    return (
        shape is type(None)
        or (inspect.isclass(shape) and issubclass(shape, Shape))
        or (
            get_origin(shape) is list
            and len(get_args(shape)) == 1
            and is_output(get_args(shape)[0])
        )
    )


def declare(
    function: Any, access: str, routes: dict[str, str | None], tool: bool, effects: str
) -> Operation:
    if getattr(function, "__observed__", False):
        raise ValueError("Observation cannot decorate an operation")
    if not access or effects not in {"NONE", "DATA", "DOCUMENT", "EXTERNAL", "FINAL"}:
        raise ValueError("Invalid operation declaration")
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
    return Operation(
        name, function, access, method, path, tool, effects, input_shape, hints["return"]
    )


def validate_routes(app: Any) -> None:
    for route in app.routes:
        if getattr(route, "path", None) in {"/mcp", "/dev"}:
            continue
        if getattr(getattr(route, "endpoint", None), "__operation__", None) is None:
            raise ValueError("Every route must be an operation")
