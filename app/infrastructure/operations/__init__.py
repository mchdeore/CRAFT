"""One checked operation entry point and observation-only private steps."""

import asyncio
import inspect
from functools import wraps
from typing import Any

from app.infrastructure.access import PUBLIC as PUBLIC
from app.infrastructure.access import SIGNED_IN as SIGNED_IN

NONE = "NONE"
DATA = "DATA"
DOCUMENT = "DOCUMENT"
EXTERNAL = "EXTERNAL"
FINAL = "FINAL"


def operation(
    access: str,
    *,
    get: str | None = None,
    post: str | None = None,
    put: str | None = None,
    patch: str | None = None,
    delete: str | None = None,
    tool: bool = False,
    effects: str = NONE,
) -> Any:
    from app.infrastructure.operations.pipeline import wrap
    from app.infrastructure.operations.registry import declare, registry

    def decorate(function: Any) -> Any:
        declaration = declare(
            function,
            access,
            {"GET": get, "POST": post, "PUT": put, "PATCH": patch, "DELETE": delete},
            tool,
            effects,
        )
        wrapper = wrap(declaration)
        from dataclasses import replace

        registered = replace(declaration, function=wrapper)
        wrapper.__dict__["__operation__"] = registered
        registry[registered.name] = registered
        return wrapper

    return decorate


def observe(name: str) -> Any:
    from app.infrastructure.errors import ApiError
    from app.infrastructure.operations.context import current_call
    from app.infrastructure.operations.pipeline import begin, finish

    def decorate(function: Any) -> Any:
        if getattr(function, "__operation__", None) is not None:
            raise ValueError("Observation cannot decorate an operation")
        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def asynchronous(*args: Any, **kwargs: Any) -> Any:
                parent = current_call.get()
                if parent is None:
                    raise RuntimeError("Observation requires an operation")
                ctx, token = begin(parent.caller)
                error = None
                try:
                    return await function(*args, **kwargs)
                except asyncio.CancelledError:
                    error = ApiError()
                    raise
                except Exception:
                    error = ApiError()
                    raise
                finally:
                    finish(ctx, token, name, "step", error)

            wrapper = asynchronous
        else:

            @wraps(function)
            def synchronous(*args: Any, **kwargs: Any) -> Any:
                parent = current_call.get()
                if parent is None:
                    raise RuntimeError("Observation requires an operation")
                ctx, token = begin(parent.caller)
                error = None
                try:
                    return function(*args, **kwargs)
                except Exception:
                    error = ApiError()
                    raise
                finally:
                    finish(ctx, token, name, "step", error)

            wrapper = synchronous
        wrapper.__dict__["__observed__"] = True
        return wrapper

    return decorate
