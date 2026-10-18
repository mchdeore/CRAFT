import asyncio
import base64
from dataclasses import replace
from xml.etree import ElementTree

from app.conversations.handlers import ConversationRef, list_files
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
    assert len(files) == 1 and files[0].id == result.file.id
    assert files[0].origin == "agent" and files[0].state == "ready"
