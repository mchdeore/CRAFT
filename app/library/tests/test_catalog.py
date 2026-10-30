"""Document inventory lists bounded, currently readable indexed metadata only."""

from typing import cast

import msgspec
import pytest
from sqlalchemy import event

from app.infrastructure.access import Caller
from app.infrastructure.errors import NotSignedIn
from app.library import handlers
from app.tests.support import rows
from app.tests.support.cube import cube_source as cube_source


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


def test_the_catalog_reads_the_same_statements_however_many_documents_it_lists(cube_source):
    source = cube_source
    engine = source["app"].state.engine
    statements = []

    def count(*_):
        statements.append(1)

    def listed():
        statements.clear()
        event.listen(engine, "before_cursor_execute", count)
        try:
            documents = catalog(source["caller"], 50).documents
        finally:
            event.remove(engine, "before_cursor_execute", count)
        return sorted(document.title for document in documents), len(statements)

    one, before = listed()
    folder = f"missions/{source['mission']}/engineering/st3-design"
    for name in ["notes", "review"]:
        source["provider"].write_content(f"{folder}/{name}.md", b"# Notes\n\nText.\n")
    rows.load(source["owner"], source["provider"])
    three, after = listed()

    assert (one, three) == (["budget"], ["budget", "notes", "review"])
    assert after == before
