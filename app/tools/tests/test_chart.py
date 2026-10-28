import asyncio
import base64
from dataclasses import replace
from xml.etree import ElementTree

import msgspec
import pytest

from app.conversations.handlers import ConversationRef, list_files
from app.infrastructure.errors import InvalidInput
from app.tools.handlers import ChartInput, make_chart

SPEC = {
    "data": {"values": [{"x": "a", "y": 1}, {"x": "b", "y": 2}, {"x": "c", "y": 3}]},
    "mark": "bar",
    "encoding": {
        "x": {"field": "x", "type": "nominal"},
        "y": {"field": "y", "type": "quantitative"},
    },
}


def test_chart_svg_and_png(chart_http):
    for kind in ["svg", "png"]:
        response = chart_http.post(
            "/api/tools/chart",
            json={"spec": SPEC, "format": kind},
            headers={"Origin": "https://testserver"},
        )
        assert response.status_code == 200
        payload = response.json()
        image = base64.b64decode(payload["contentBase64"], validate=True)
        if kind == "svg":
            assert ElementTree.fromstring(image).tag.endswith("svg")
        else:
            assert image.startswith(b"\x89PNG\r\n\x1a\n")
        assert payload["file"] is None


def test_chart_refuses_urls_and_large_data(chart_http):
    for data in [{"url": "https://example.invalid/data.json"}, {"values": [{}] * 5001}]:
        response = chart_http.post(
            "/api/tools/chart",
            json={"spec": {**SPEC, "data": data}, "format": "svg"},
            headers={"Origin": "https://testserver"},
        )
        assert response.status_code == 400
    image = {
        **SPEC,
        "mark": "image",
        "encoding": {"url": {"value": "https://example.invalid/image.png"}},
    }
    assert (
        chart_http.post(
            "/api/tools/chart",
            json={"spec": image, "format": "svg"},
            headers={"Origin": "https://testserver"},
        ).status_code
        == 400
    )


def test_chart_saved_to_workspace(chat):
    caller = replace(chat["caller"], origin="python", conversation_id=chat["thread"])
    result = asyncio.run(make_chart(caller, ChartInput(spec=SPEC, format="png")))
    assert result.file is not None and result.content_base64 is None
    files = list_files(caller, ConversationRef(id=chat["thread"]))
    assert len(files) == 4 and result.file.id in {f.id for f in files}
    assert all(f.origin == "agent" and f.state == "ready" for f in files)


def a_source(chat):
    from app.library.handlers import LibrarySearch, search

    hits = search(chat["caller"], LibrarySearch(query="power", kinds=["chunk"], limit=1)).hits
    assert hits
    return hits[0].handle


def test_chart_keeps_four_files_with_its_sources(chat):
    handle = a_source(chat)
    caller = replace(chat["caller"], origin="python", conversation_id=chat["thread"])
    result = asyncio.run(
        make_chart(caller, ChartInput(spec=SPEC, title="Power by item", sources=[handle]))
    )
    assert result.title == "Power by item" and result.citations == [handle]
    assert result.simulated is handle.simulated
    assert result.files is not None and result.file == result.files.svg
    stems = {f.name.split(".", 1)[0] for f in files_of(result)}
    assert len(stems) == 1
    stored = {f.id: f for f in list_files(caller, ConversationRef(id=chat["thread"]))}
    assert {f.id for f in files_of(result)} <= set(stored)
    assert [f.media_type for f in files_of(result)] == [
        "image/svg+xml",
        "image/png",
        "application/json",
        "text/csv",
    ]


def files_of(result):
    assert result.files is not None
    return [result.files.svg, result.files.png, result.files.spec, result.files.csv]


def test_chart_refuses_a_source_that_does_not_match(chat):
    handle = a_source(chat)
    forged = replace_handle(handle, quote_sha256="0" * 64)
    with pytest.raises(InvalidInput):
        asyncio.run(make_chart(chat["caller"], ChartInput(spec=SPEC, sources=[forged])))


def replace_handle(handle, **fields):
    return msgspec.structs.replace(handle, **fields)


def test_chart_table_lists_inline_rows():
    from app.tools.service import chart_table, titled

    layered = {"layer": [SPEC, {**SPEC, "data": {"values": [{"x": "d", "z": [1]}]}}]}
    assert chart_table(layered).splitlines() == ["x,y,z", "a,1,", "b,2,", "c,3,", "d,,[1]"]
    assert titled(SPEC, None)[1] == "Chart"
    assert titled({**SPEC, "title": {"text": "Mass"}}, None)[1] == "Mass"
