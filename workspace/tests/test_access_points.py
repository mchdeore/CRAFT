"""Generated-data checks of the route access points, file helpers, documents and guard."""

import json
import socket
import subprocess
import sys

import httpx
import pytest


@pytest.fixture
def api(monkeypatch):
    """Route every access point through an in-memory transport and record requests."""
    import craft.access as access

    requests = []
    replies = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        key = (request.method, request.url.path)
        status, body = replies.get(key, (200, {}))
        return httpx.Response(status, json=body)

    monkeypatch.setenv("CRAFT_API_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(access, "_client", None)
    monkeypatch.setattr(access, "_session_token", "synthetic-workspace-token")
    original = httpx.Client

    def client(**options):
        return original(transport=httpx.MockTransport(handler), **options)

    monkeypatch.setattr(httpx, "Client", client)
    yield requests, replies
    monkeypatch.setattr(access, "_client", None)


def test_access_points_use_routes_with_the_workspace_token(api):
    import craft

    requests, replies = api
    handle = {
        "item": "itm_012345abcdef",
        "version": 2,
        "block": 4,
        "locator": "heading=1;block=4",
        "quoteSha256": "ab" * 32,
        "simulated": True,
    }
    replies[("POST", "/api/library/search")] = (
        200,
        {"hits": [{"kind": "chunk", "title": "Generated", "handle": handle, "score": 0.5}]},
    )
    hits = craft.search("radiator margin", missions=["msn-sim-01"], limit=3)
    assert hits.citations == [handle]
    assert list(hits.df["title"]) == ["Generated"]
    craft.cube.find(("mass_dry", "le", 150, "kg"), stage="current", known_at="2026-10-01")
    craft.cube.mission("msn-sim-01", at="2026-03-15")
    craft.document("itm_012345abcdef", version=2)
    craft.chunks("itm_012345abcdef", 2, from_block=3)

    first, find, mission, document, chunks = requests
    assert first.headers["authorization"] == "Bearer synthetic-workspace-token"
    assert "cookie" not in first.headers
    assert json.loads(first.content) == {
        "query": "radiator margin",
        "missions": ["msn-sim-01"],
        "limit": 3,
    }
    assert (find.method, find.url.path) == ("POST", "/api/cube/find")
    assert json.loads(find.content) == {
        "conditions": [{"parameter": "mass_dry", "op": "le", "value": "150", "unit": "kg"}],
        "stage": "current",
        "knownAt": "2026-10-01",
    }
    assert (mission.method, mission.url.path) == ("GET", "/api/cube/missions/msn-sim-01")
    assert dict(mission.url.params) == {"at": "2026-03-15"}
    assert dict(document.url.params) == {"version": "2"}
    assert dict(chunks.url.params) == {"version": "2", "fromBlock": "3", "toBlock": "22"}


def test_route_errors_name_the_code_and_request(api):
    import craft

    _, replies = api
    replies[("GET", "/api/cube/values/missing")] = (
        404,
        {"code": "not_found", "message": "Not found", "requestId": "req-1"},
    )
    with pytest.raises(craft.CraftError, match="not_found: Not found \\(request req-1\\)"):
        craft.cube.evidence("missing")


def test_compare_table_is_one_row_per_mission(api):
    import craft

    _, replies = api
    value = {"low": 450, "unit": "W"}
    replies[("POST", "/api/cube/compare")] = (
        200,
        {
            "parameters": ["power_peak"],
            "rows": [
                {"mission": {"key": "msn-sim-01"}, "values": [value]},
                {"mission": {"key": "msn-sim-02"}, "values": [None]},
            ],
        },
    )
    table = craft.cube.compare(["msn-sim-01", "msn-sim-02"], ["power_peak"]).df
    assert list(table["mission"]) == ["msn-sim-01", "msn-sim-02"]
    assert table["power_peak"][0] == 450 and table["power_peak_unit"][0] == "W"
    assert table[["power_peak", "power_peak_unit"]].iloc[1].isna().all()


def test_missing_token_explains_how_to_sign_in(monkeypatch, tmp_path):
    import craft
    import craft.access as access

    monkeypatch.setenv("CRAFT_API_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(access, "_client", None)
    monkeypatch.setattr(access, "_session_token", None)
    monkeypatch.setattr(access, "TOKEN_FILE", tmp_path / "absent")
    with pytest.raises(craft.CraftError, match="craft.login"):
        craft.search("anything")


def test_package_exposes_no_generic_dispatcher():
    import craft

    assert not hasattr(craft, "connect")
    assert not hasattr(craft.Craft, "call")
    assert "call" not in craft.__all__
    assert "analysis" in craft.__all__


@pytest.fixture
def root(monkeypatch, tmp_path):
    import craft._root as owned

    path = tmp_path.resolve() / "owner"
    path.mkdir()
    monkeypatch.setattr(owned, "USER_ROOT", path)
    return path


def test_files_read_and_save_by_extension(root):
    pytest.importorskip("duckdb", reason="needs the analysis extra")
    import craft
    import pandas as pd

    frame = pd.DataFrame({"region": ["east", "west"], "amount": [3, 4]})
    assert craft.save(frame, "data/sales.csv") == "data/sales.csv"
    assert craft.save(frame, "data/sales.xlsx") == "data/sales.xlsx"
    assert craft.save({"total": 7}, "results/total.json") == "results/total.json"
    assert craft.read("data/sales.csv").equals(frame)
    assert craft.read("data/sales.xlsx").equals(frame)
    assert craft.read("results/total.json") == {"total": 7}
    assert craft.ls() == ["data/", "results/"]
    assert craft.find("*.csv") == ["data/sales.csv"]
    total = craft.sql("select sum(amount) as total from sales", sales=frame)
    assert int(total["total"][0]) == 7
    from_file = craft.sql("select count(*) as n from 'data/sales.csv'")
    assert int(from_file["n"][0]) == 2
    with pytest.raises(ValueError):
        craft.read("../outside.csv")
    with pytest.raises(ValueError):
        craft.save(frame, "no-extension")


def test_analysis_helpers_shape_tables_and_save_charts(root):
    pytest.importorskip("matplotlib", reason="needs the analysis extra")
    pytest.importorskip("pandas", reason="needs the analysis extra")
    import matplotlib  # pyright: ignore[reportMissingImports]

    matplotlib.use("Agg")
    import craft
    import pandas as pd

    sales = pd.DataFrame(
        {
            "region": ["north", "south", "north", "west"],
            "amount": [5, 8, 6, 2],
            "quarter": ["Q1", "Q1", "Q2", "Q1"],
        }
    )
    totals = craft.analysis.totals(sales, by="region", value="amount")
    assert totals.to_dict("records")[0] == {"region": "north", "amount": 11}
    top = craft.analysis.table(totals, rename={"amount": "total_amount"}, top=2)
    assert list(top.columns) == ["region", "total_amount"]
    saved = craft.analysis.chart(
        top, "region", "total_amount", kind="bar", save_as="charts/top.png"
    )
    assert saved == "charts/top.png"
    assert craft.path(saved).is_file()


def test_docs_editable_copy_makes_safe_derivatives(root):
    pytest.importorskip("pandas", reason="needs the analysis extra")
    import craft
    import pandas as pd

    frame = pd.DataFrame({"region": ["east", "west"], "amount": [3, 4]})
    craft.save(frame, "imports/budget.xlsx")
    original = craft.path("imports/budget.xlsx").read_bytes()
    editable = craft.docs.editable_copy("imports/budget.xlsx")
    assert editable == "imports/budget-edit.xlsx"
    assert craft.path(editable).read_bytes() == original

    edited = craft.read(editable)
    edited["amount"] = edited["amount"] * 2
    craft.save(edited, editable)
    assert list(craft.read("imports/budget.xlsx")["amount"]) == [3, 4]
    assert list(craft.read(editable)["amount"]) == [6, 8]
    assert craft.docs.editable_copy("imports/budget.xlsx") == "imports/budget-edit-2.xlsx"

    craft.save(frame, "imports/sales.csv")
    assert craft.docs.editable_copy("imports/sales.csv", save_as="working/sales-copy.csv") == (
        "working/sales-copy.csv"
    )
    with pytest.raises(FileExistsError):
        craft.docs.editable_copy("imports/sales.csv", save_as="working/sales-copy.csv")
    with pytest.raises(ValueError):
        craft.docs.editable_copy("imports/budget.xlsx", save_as="imports/budget.csv")
    craft.save("plain text", "notes/readme.txt")
    with pytest.raises(ValueError):
        craft.docs.editable_copy("notes/readme.txt", save_as="notes/readme-edit.md")


def test_editable_copy_skips_dangling_symlink(root, tmp_path):
    import craft

    craft.path("source.csv").write_bytes(b"name,value\nSynthetic,1\n")
    outside = tmp_path / "outside.csv"
    craft.path("source-edit.csv").symlink_to(outside)
    assert craft.docs.editable_copy("source.csv") == "source-edit-2.csv"
    assert not outside.exists()
    with pytest.raises(ValueError, match="Symlinks"):
        craft.docs.editable_copy("source.csv", save_as="source-edit.csv")


def test_editable_copy_refuses_racing_destination(root, monkeypatch):
    from pathlib import Path

    import craft

    craft.path("source.csv").write_bytes(b"original")
    target = craft.path("copy.csv")
    original_open = Path.open

    def racing_open(path, mode="r", *args, **kwargs):
        if path == target and mode == "xb":
            target.write_bytes(b"other writer")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing_open)
    with pytest.raises(FileExistsError):
        craft.docs.editable_copy("source.csv", save_as="copy.csv")
    assert target.read_bytes() == b"other writer"


def test_word_and_slides_round_trip(root):
    for module in ("matplotlib", "docx", "pptx"):
        pytest.importorskip(module, reason="needs the analysis extra")
    import matplotlib  # pyright: ignore[reportMissingImports]

    matplotlib.use("Agg")
    import craft
    import matplotlib.pyplot as plt  # pyright: ignore[reportMissingImports]
    import pandas as pd

    table = pd.DataFrame({"parameter": ["mass_dry"], "value": [120]})
    figure, axes = plt.subplots()
    axes.bar(["a", "b"], [1, 2])
    craft.docs.report(
        "reports/summary.docx", "Generated summary", "# Findings", "- One finding", table, figure
    )
    plt.close(figure)
    text = craft.read("reports/summary.docx")
    assert "# Findings" in text and "- One finding" in text and "| mass_dry | 120 |" in text
    assert craft.docs.word_tables("reports/summary.docx")[0].to_dict("records") == [
        {"parameter": "mass_dry", "value": "120"}
    ]
    assert craft.docs.replace_in_word("reports/summary.docx", "One finding", "Two findings") == 1
    assert "Two findings" in craft.read("reports/summary.docx")
    craft.docs.slides("reports/deck.pptx", "Deck", ("Notes", "first\nsecond"), ("Table", table))
    slides = craft.read("reports/deck.pptx")
    assert [slide["title"] for slide in slides] == ["Deck", "Notes", "Table"]
    assert "second" in slides[1]["text"]


GUARD_PROBE = """
import socket, subprocess, sys
import craft_guard
assert craft_guard.installed()
for attempt in (
    lambda: socket.create_connection(("192.0.2.10", 80), timeout=1),
    lambda: socket.getaddrinfo("example.invalid", 80),
    lambda: subprocess.run(["true"]),
):
    try:
        attempt()
    except craft_guard.OutboundBlocked:
        continue
    sys.exit("not blocked")
server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen()
craft_guard.allow("api", "127.0.0.1", server.getsockname()[1])
socket.create_connection(("127.0.0.1", server.getsockname()[1]), timeout=1).close()
craft_guard.revoke("api")
try:
    socket.create_connection(("127.0.0.1", server.getsockname()[1]), timeout=1)
except craft_guard.OutboundBlocked:
    print("guarded")
"""


def test_guard_blocks_everything_but_allowed_endpoints():
    result = subprocess.run(
        [sys.executable, "-c", GUARD_PROBE],
        env={"CRAFT_GUARD": "1", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(__import__("pathlib").Path(__file__).parents[1] / "src"),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "guarded"


def test_guard_is_off_without_the_flag():
    import craft_guard

    if craft_guard.installed():
        pytest.skip("this test process already runs guarded")
    socket.getaddrinfo("127.0.0.1", 80)
