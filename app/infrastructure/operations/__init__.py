"""One checked operation entry point and observation-only private steps."""

import asyncio
import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from app.infrastructure.access import PUBLIC as PUBLIC
from app.infrastructure.access import SIGNED_IN as SIGNED_IN
from app.infrastructure.access import Caller
from app.infrastructure.operations.registry import Job as Job

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
    prepare_approved: Callable[[Caller, Any], object] | None = None,
    job: Job | None = None,
    grants: str | None = None,
    granted: str | None = None,
    granted_by: str | None = None,
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
            prepare_approved,
            job,
        )
        from dataclasses import replace

        if grants is not None or granted is not None or granted_by is not None:
            fields = declaration.input.__struct_fields__ if declaration.input else ()
            if (
                effects != EXTERNAL
                or (grants is not None and granted is not None)
                or (grants or granted) not in fields
            ):
                raise ValueError("A notebook grant names an input field of an external effect")
            granting = registry.get(granted_by or "")
            if (granted is None) != (granted_by is None) or (
                granted_by is not None and (granting is None or granting.grants is None)
            ):
                raise ValueError("A granted call names the operation whose approval grants it")
            declaration = replace(
                declaration, grants=grants, granted=granted, granted_by=granted_by
            )

        if job is not None:
            from app.infrastructure.operations.jobs import Queued, deferring

            # Calling a job operation queues it; the worker runs the declared function.
            declaration = replace(
                declaration, function=deferring(declaration), output=Queued, run=function
            )
        wrapper = wrap(declaration)
        registered = replace(declaration, function=wrapper)
        wrapper.__dict__["__operation__"] = registered
        registry[registered.name] = registered
        if job is not None:
            from app.infrastructure.operations.jobs import register

            register(registered)
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
