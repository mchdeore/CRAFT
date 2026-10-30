"""The capability catalog as a caller sees it."""

import asyncio
import inspect
from collections.abc import Coroutine
from typing import Any, cast

from app.infrastructure.access import Caller
from app.tools import handlers


def catalog(caller: Caller) -> handlers.CapabilityCatalog:
    operation = getattr(handlers, "list_capabilities", None)
    assert callable(operation), "Missing authenticated list_capabilities operation"
    result = operation(caller)
    if inspect.isawaitable(result):
        result = asyncio.run(cast(Coroutine[Any, Any, handlers.CapabilityCatalog], result))
    return cast(handlers.CapabilityCatalog, result)
