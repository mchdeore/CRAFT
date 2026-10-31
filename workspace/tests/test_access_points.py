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
    monkeypatch.delenv("JUPYTERHUB_API_TOKEN", raising=False)  # outside a Hub workspace
    monkeypatch.setattr(access, "_client", None)
    monkeypatch.setattr(access, "_session_token", None)
    monkeypatch.setattr(access, "TOKEN_FILE", tmp_path / "absent")
    with pytest.raises(craft.CraftError, match="craft.login"):
        craft.whoami()


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


def fake_soffice(tmp_path, monkeypatch, body):
    """LibreOffice as a shell script running ``body``; its arguments land in the returned file."""
    from craft import system

    arguments, script = tmp_path / "soffice-arguments", tmp_path / "soffice"
    script.write_text(f'#!/bin/sh\necho "$@" > "{arguments}"\n{body}\n')
    script.chmod(0o755)
    monkeypatch.setitem(system._found, "soffice", str(script))
    return arguments


def test_a_conversion_that_makes_no_pdf_fails_and_each_process_has_its_profile(
    root, tmp_path, monkeypatch
):
    import os

    import craft

    arguments = fake_soffice(tmp_path, monkeypatch, "exit 0")  # exits 0 having converted nothing
    (root / "memo.docx").write_bytes(b"generated")
    with pytest.raises(RuntimeError, match="made no PDF of memo.docx"):
        craft.docs.to_pdf("memo.docx")
    assert f"/craft-office-profile-{os.getpid()} " in arguments.read_text()


def test_a_conversion_past_its_time_stops_everything_it_started(root, tmp_path, monkeypatch):
    import time

    import craft

    child = tmp_path / "child.pid"
    fake_soffice(tmp_path, monkeypatch, f'sleep 60 & echo $! > "{child}"; wait')
    (root / "memo.docx").write_bytes(b"generated")
    with pytest.raises(subprocess.TimeoutExpired):
        craft.docs.to_pdf("memo.docx", timeout=0.5)
    pid, deadline = child.read_text().strip(), time.monotonic() + 5
    while time.monotonic() < deadline:
        state = subprocess.run(["ps", "-o", "stat=", "-p", pid], capture_output=True, text=True)
        if not state.stdout.strip() or state.stdout.strip().startswith("Z"):
            return  # gone, or dead and not yet reaped
        time.sleep(0.05)
    pytest.fail("what LibreOffice started outlived its timeout")


def test_vega_charts_take_inline_data_naming_urls_and_load_none(root):
    import craft

    encoding = {
        "x": {"field": "source", "type": "nominal"},
        "y": {"field": "url_count", "type": "quantitative"},
    }
    inline = {"data": {"values": [{"source": "curl", "url_count": 3}]}, "mark": "bar"}
    chart = craft.vega({**inline, "encoding": encoding})
    assert isinstance(chart, craft.analysis.VegaChart) and "<svg" in chart.svg
    remote = {"data": {"url": "https://example.com/rows.csv"}, "mark": "bar", "encoding": encoding}
    with pytest.raises(ValueError, match="inline"):
        craft.vega(remote)
    with pytest.raises(ValueError, match="not allowed"):  # nested data: vl-convert loads none
        craft.vega({"layer": [remote]})


GUARD_REFRESH_PROBE = """
import socket
import craft_guard
assert craft_guard.installed()
server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen()
port = server.getsockname()[1]
answers, lookups, clock = ["127.0.0.2"], [], [1000.0]

def resolve(host, port, *args, **kwargs):  # the cluster's DNS for the API's Service
    lookups.append(host)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[0], port))]

def connect():
    attempt = socket.socket()
    try:
        attempt.connect(("127.0.0.1", port))
        return "connected"
    except craft_guard.OutboundBlocked:
        return "blocked"
    finally:
        attempt.close()

socket.getaddrinfo = resolve
craft_guard.monotonic = lambda: clock[0]
craft_guard.allow("api", "craft-api", port)
answers[0] = "127.0.0.1"  # the Service moved
assert connect() == "blocked" and lookups == ["craft-api"], lookups  # too soon to look again
clock[0] += craft_guard.REFRESH_SECONDS
assert connect() == "connected" and lookups == ["craft-api"] * 2, lookups
assert connect() == "connected" and lookups == ["craft-api"] * 2, lookups
print("refreshed")
"""


def test_the_guard_looks_up_a_moved_api_again_at_most_every_30_seconds():
    result = subprocess.run(
        [sys.executable, "-c", GUARD_REFRESH_PROBE],
        env={"CRAFT_GUARD": "1", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(__import__("pathlib").Path(__file__).parents[1] / "src"),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refreshed"


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


def test_a_hub_workspace_swaps_its_server_token_and_renews_once(hub_workspace, monkeypatch):
    import craft

    requests, replies = hub_workspace
    swaps = []

    def post(url, **options):
        swaps.append((url, options["headers"]))
        return httpx.Response(200, json={"token": f"swapped-{len(swaps)}"})

    monkeypatch.setattr(httpx, "post", post)
    craft.whoami()
    craft.whoami()
    assert swaps == [
        (
            "http://127.0.0.1:8000/api/workspace-tokens/hub",
            {"Authorization": "token generated0server0token"},
        )
    ]
    assert [r.headers["authorization"] for r in requests] == ["Bearer swapped-1"] * 2

    # An expired swapped token is swapped again once, then the call goes through.
    replies[("GET", "/api/me")] = (401, {"code": "not_signed_in"})
    with pytest.raises(craft.CraftError):
        craft.whoami()
    assert len(swaps) == 2  # once, not in a loop
    assert requests[-1].headers["authorization"] == "Bearer swapped-2"


@pytest.fixture
def hub_workspace(api, monkeypatch, tmp_path):
    """A kernel in a Hub workspace: no login, no token file, the server's own Hub token."""
    import craft.access as access

    monkeypatch.setattr(access, "_session_token", None)
    monkeypatch.setattr(access, "_hub_token", None)
    monkeypatch.setattr(access, "TOKEN_FILE", tmp_path / "missing")
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "generated0server0token")
    yield api
    monkeypatch.setattr(access, "_hub_token", None)
    monkeypatch.setattr(access, "_client", None)


def test_a_refused_swap_says_how_to_sign_in_and_never_shows_the_hub_token(
    hub_workspace, monkeypatch
):
    import craft

    requests, _ = hub_workspace
    monkeypatch.setattr(
        httpx, "post", lambda url, **options: httpx.Response(401, json={"code": "not_signed_in"})
    )
    with pytest.raises(craft.CraftError) as refused:
        craft.whoami()
    assert "craft.login" in str(refused.value) and "401" in str(refused.value)
    assert "generated0server0token" not in str(refused.value)
    assert requests == []  # no API request goes out without a token


def test_login_and_a_token_file_come_before_the_swap(hub_workspace, monkeypatch, tmp_path):
    import craft
    import craft.access as access

    requests, replies = hub_workspace
    swaps = []
    monkeypatch.setattr(httpx, "post", lambda url, **options: swaps.append(url))
    token_file = tmp_path / "workspace-token"
    token_file.write_text("generated-file-token\n")
    monkeypatch.setattr(access, "TOKEN_FILE", token_file)
    craft.whoami()
    monkeypatch.setattr(access, "_client", None)
    craft.login("generated-login-token")
    replies[("GET", "/api/me")] = (401, {"code": "not_signed_in"})
    with pytest.raises(craft.CraftError):
        craft.whoami()  # a refused login token is the person's to replace, never swapped
    assert [r.headers["authorization"] for r in requests] == [
        "Bearer generated-file-token",
        "Bearer generated-login-token",
    ]
    assert swaps == []


def test_threads_that_hit_an_expired_swapped_token_share_one_new_swap(hub_workspace, monkeypatch):
    import threading

    import craft
    import craft.access as access

    swaps = []

    def post(url, **options):
        swaps.append(url)
        return httpx.Response(200, json={"token": f"swapped-{len(swaps)}"})

    def handler(request):  # CRAFT refuses the first swapped token from now on
        if request.headers["authorization"] == "Bearer swapped-1":
            return httpx.Response(401, json={"code": "not_signed_in"})
        return httpx.Response(200, json={"id": "generated-person"})

    monkeypatch.setattr(httpx, "post", post)
    craft.whoami()  # the first swap, while swapped-1 was still good
    client = access._http()
    monkeypatch.setattr(client, "_transport", httpx.MockTransport(handler))
    gate = threading.Barrier(6)
    failures = []

    def call():
        gate.wait()
        try:
            assert craft.whoami().data["id"] == "generated-person"
        except Exception as error:  # noqa: BLE001 - collected for the assertion below
            failures.append(error)

    threads = [threading.Thread(target=call) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert failures == []
    assert len(swaps) == 2  # the first swap and exactly one renewal for all six threads
