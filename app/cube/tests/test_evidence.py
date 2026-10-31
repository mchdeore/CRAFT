"""Value evidence takes its status and quote from one check of the stored block."""

from pathlib import Path

from app.cube.handlers import ValueRef, get_value_evidence
from app.library import convert


def test_value_evidence_shows_the_stored_quote_without_converting(cube, monkeypatch):
    reference = ValueRef(id=cube["add"]())

    def refuse(content, filename):
        raise AssertionError("an original was converted")

    monkeypatch.setattr(convert, "convert", refuse)
    evidence = get_value_evidence(cube["caller"], reference)
    assert (evidence.citation_status, evidence.quote) == ("current", "| Peak power | 400 | W |")

    # An original changed in place answers `changed` and still shows the quote the value cites.
    snapshot = Path(cube["settings"].provider_root, ".provider/versions", cube["item"], "1")
    snapshot.write_bytes(snapshot.read_bytes().replace(b"# 1 Budget", b"# 1 Power budget"))
    evidence = get_value_evidence(cube["caller"], reference)
    assert (evidence.citation_status, evidence.quote) == ("changed", "| Peak power | 400 | W |")
