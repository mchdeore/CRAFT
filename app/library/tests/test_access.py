import pytest

from app.infrastructure.errors import NotFound
from app.library.handlers import DocumentRef, LibrarySearch, get_document, search


def test_access_rules(library) -> None:
    demo = search(library["callers"]["demo"], LibrarySearch(query="simulated")).hits
    assert demo and all(
        h.tags.mission is None or h.tags.mission == "msn-sim-01" or h.tags.silo == "engineering"
        for h in demo
    )
    restricted = search(library["callers"]["restricted"], LibrarySearch(query="simulated"))
    assert restricted.hits and all(h.tags.mission is None for h in restricted.hits)
    assert not hasattr(restricted, "hidden")
    admin = search(library["callers"]["admin"], LibrarySearch(query="budget reserve")).hits
    hidden = next(h for h in admin if h.tags.silo == "budget")
    with pytest.raises(NotFound):
        get_document(library["callers"]["demo"], DocumentRef(item=hidden.handle.item))
