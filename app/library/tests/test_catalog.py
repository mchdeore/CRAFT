"""Document inventory lists bounded, currently readable indexed metadata only."""

from typing import cast

import msgspec
import pytest

from app.infrastructure.access import Caller
from app.infrastructure.errors import NotSignedIn
from app.library import handlers


def catalog(caller: Caller, limit: int | None = None) -> handlers.DocumentCatalog:
    operation = getattr(handlers, "list_documents", None)
    request = getattr(handlers, "DocumentList", None)
    assert callable(operation), "Missing authenticated list_documents operation"
    assert request is not None, "Missing bounded DocumentList request"
    return cast(
        handlers.DocumentCatalog,
        operation(caller, request() if limit is None else request(limit=limit)),
    )


def test_catalog_rejects_unsigned_callers_and_invalid_limits():
    with pytest.raises(NotSignedIn):
        catalog(Caller())
    request = getattr(handlers, "DocumentList", None)
    assert request is not None, "Missing bounded DocumentList request"
    for invalid in [0, 51, -1, "25"]:
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({"limit": invalid}, request, strict=True)
