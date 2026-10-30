"""Agent code runs and file changes wait for approval and dispatch once to the own workspace."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import httpx
import msgspec
import pytest

from app.approvals.handlers import (
    ApprovalRef,
    ApprovalRequest,
    Decision,
    decide_approval,
    read_approval,
    request_approval,
)
from app.infrastructure import hub, workspaces
from app.infrastructure.errors import ApiError, Forbidden, InvalidInput, NotFound
from app.infrastructure.operations.pipeline import ApprovalRequired
from app.tests.support.workspace import KERNEL
from app.workspace.handlers import (
    EditNotebook,
    RunCode,
    RunNotebook,
    SaveChart,
    WriteFile,
    edit_notebook,
    run_code,
    run_notebook,
    save_chart,
)
from app.workspace.handlers import write_file as write_file_operation


def proposal(context, operation, value, approve: bool | None = True):
    trusted = replace(
        context["caller"],
        origin="mcp",
        run_id=context["run"],
        conversation_id=context["conversation"],
    )
    proposed = request_approval(
        trusted,
        ApprovalRequest(
            run_id=context["run"],
            conversation_id=context["conversation"],
            operation=operation.__operation__.name,
            arguments=msgspec.to_builtins(value),
            evidence=[],
            reasoning="Run a generated calculation.",
            retrieval_path=[],
            interrupt_id="generated-" + uuid4().hex,
        ),
    )
    if approve is not None:
        decide_approval(
            replace(context["caller"], origin="http"), Decision(id=proposed.id, approve=approve)
        )
    return trusted, proposed.id


def status(context, identifier):
    return read_approval(
        replace(context["caller"], origin="http"), ApprovalRef(id=identifier)
    ).status


def test_agent_code_run_waits_for_approval(context, workspace):
    calls, _ = workspace
    value = RunCode(code="1 + 1")
    trusted, identifier = proposal(context, run_code, value, approve=None)
    with pytest.raises(ApprovalRequired):
        asyncio.run(run_code(trusted, value))
    assert calls == []
    assert status(context, identifier) == "pending"


def test_approved_code_runs_once_in_the_owner_kernel(context, workspace):
    calls, _ = workspace
    value = RunCode(code="1 + 1")
    trusted, identifier = proposal(context, run_code, value)
    result = asyncio.run(run_code(trusted, value))
    assert result.outputs == ["2\n"] and result.status == "ok"
    assert calls == [("run", KERNEL, "1 + 1")]
    assert status(context, identifier) == "executed"
    with pytest.raises(ApprovalRequired):
        asyncio.run(run_code(trusted, value))
    assert len(calls) == 1


def test_approval_covers_only_the_exact_code(context, workspace):
    calls, _ = workspace
    trusted, _ = proposal(context, run_code, RunCode(code="1 + 1"))
    with pytest.raises(ApprovalRequired):
        asyncio.run(run_code(trusted, RunCode(code="import shutil")))
    assert calls == []


def test_rejected_change_is_never_written(context, workspace):
    calls, _ = workspace
    value = WriteFile(path="scripts/fit.py", content="print(1)\n")
    trusted, identifier = proposal(context, write_file_operation, value, approve=False)
    with pytest.raises(ApprovalRequired):
        asyncio.run(write_file_operation(trusted, value))
    assert calls == []
    assert status(context, identifier) == "rejected"


def test_requester_runs_own_code_without_approval(context, workspace):
    calls, _ = workspace
    result = asyncio.run(run_code(replace(context["caller"], origin="http"), RunCode(code="2")))
    assert result.status == "ok" and calls == [("run", KERNEL, "2")]


def test_workspace_token_cannot_run_or_write(context, workspace):
    calls, _ = workspace
    token_caller = replace(context["caller"], origin="workspace")
    with pytest.raises(Forbidden):
        asyncio.run(run_code(token_caller, RunCode(code="2")))
    with pytest.raises(Forbidden):
        asyncio.run(write_file_operation(token_caller, WriteFile(path="a.py", content="")))
    assert calls == []


def test_paths_stay_inside_the_workspace(context, workspace):
    own = replace(context["caller"], origin="http")
    for path in ["../outside.py", "/etc/passwd", ".hidden/x.py", "a//b.py", "a\\b.py"]:
        with pytest.raises(InvalidInput):
            asyncio.run(write_file_operation(own, WriteFile(path=path, content="")))


def test_notebook_cells_insert_replace_and_delete(context, workspace):
    _, files = workspace
    own = replace(context["caller"], origin="http")

    def edit(**fields):
        return asyncio.run(edit_notebook(own, EditNotebook(path="work/fit.ipynb", **fields)))

    assert edit(action="insert", index=0, source="x = 1").cells == 1
    assert edit(action="insert", index=1, source="# Notes", cell_type="markdown").cells == 2
    first = files["work/fit.ipynb"]["cells"][0]["id"]
    assert edit(action="replace", index=0, source="x = 2").cells == 2
    cell = files["work/fit.ipynb"]["cells"][0]
    assert cell["id"] == first and cell["source"] == "x = 2" and cell["outputs"] == []
    with pytest.raises(InvalidInput):
        edit(action="delete", index=5)
    assert edit(action="delete", index=1).cells == 1


def test_a_workspace_outage_is_not_a_missing_notebook(context, workspace):
    calls, _ = workspace
    own = replace(context["caller"], origin="http")
    with pytest.raises(NotFound):
        asyncio.run(edit_notebook(own, EditNotebook(path="gone.ipynb", action="delete", index=0)))
    with pytest.raises(NotFound):
        asyncio.run(run_notebook(own, RunNotebook(path="gone.ipynb")))
    outage = EditNotebook(path="outage.ipynb", action="insert", index=0)
    for call in (run_notebook(own, RunNotebook(path=outage.path)), edit_notebook(own, outage)):
        with pytest.raises(ApiError) as error:  # a 500, not a 404, and nothing written
            asyncio.run(call)
        assert error.value.status == 500
    assert calls == []


def test_direct_workspace_tools_are_read_only():
    from app.agent.workspace import WorkspaceEndpoint

    endpoint = WorkspaceEndpoint(
        "http://127.0.0.1:4040/mcp",
        {},
        ("list_files", "use_notebook", "read_notebook", "execute_code", "overwrite_cell_source"),
    )
    assert endpoint.tools == ("list_files", "read_notebook")


def test_chart_saves_svg_spec_and_csv(context, workspace):
    calls, files = workspace
    spec = {
        "data": {"values": [{"x": "a", "y": 1}]},
        "mark": "bar",
        "encoding": {
            "x": {"field": "x", "type": "nominal"},
            "y": {"field": "y", "type": "quantitative"},
        },
    }
    own = replace(context["caller"], origin="http")
    saved = asyncio.run(save_chart(own, SaveChart(name="peak-power", spec=spec, title="Peak")))
    assert saved.svg == "outputs/charts/peak-power.svg"
    assert files[saved.svg].lstrip().startswith("<svg")
    assert '"title": "Peak"' in files[saved.spec]
    assert files[saved.csv] == "x,y\na,1\n"
    value = SaveChart(name="peak-power", spec=spec)
    trusted, _ = proposal(context, save_chart, value, approve=None)
    calls.clear()
    with pytest.raises(ApprovalRequired):
        asyncio.run(save_chart(trusted, value))
    assert calls == []


def test_approving_a_notebook_edit_grants_later_edits_without_asking(context, workspace):
    from app.approvals.handlers import (
        GrantQuery,
        GrantRef,
        list_notebook_grants,
        revoke_notebook_grant,
    )
    from app.workspace.handlers import GrantedRun, edit_granted_notebook, run_in_granted_notebook

    calls, _ = workspace
    first = EditNotebook(path="work/fit.ipynb", action="insert", index=0, source="x = 1")
    trusted, identifier = proposal(context, edit_notebook, first)
    assert asyncio.run(edit_notebook(trusted, first)).cells == 1
    own = replace(context["caller"], origin="http")
    grants = list_notebook_grants(own, GrantQuery(conversation_id=context["conversation"]))
    assert [(g.path, g.approval_id) for g in grants] == [("work/fit.ipynb", identifier)]

    second = EditNotebook(path="work/fit.ipynb", action="insert", index=1, source="x")
    assert asyncio.run(edit_granted_notebook(trusted, second)).cells == 2
    # An approved edit is no approval to run code.
    with pytest.raises(ApprovalRequired):
        asyncio.run(run_in_granted_notebook(trusted, GrantedRun(notebook=first.path, code="x")))
    other = EditNotebook(path="work/other.ipynb", action="insert", index=0, source="y")
    with pytest.raises(ApprovalRequired):
        asyncio.run(edit_granted_notebook(trusted, other))

    revoke_notebook_grant(own, GrantRef(id=grants[0].id))
    with pytest.raises(ApprovalRequired):
        asyncio.run(edit_granted_notebook(trusted, second))
    assert [call[0] for call in calls] == ["notebook", "notebook"]


def test_an_approved_run_adds_its_own_grant_that_covers_no_edit(context, workspace):
    from app.approvals.handlers import GrantQuery, list_notebook_grants
    from app.workspace.handlers import GrantedRun, edit_granted_notebook, run_in_granted_notebook

    calls, _ = workspace
    edit = EditNotebook(path="work/fit.ipynb", action="insert", index=0, source="x = 1")
    trusted, edited = proposal(context, edit_notebook, edit)
    asyncio.run(edit_notebook(trusted, edit))
    value = RunCode(code="x", notebook=edit.path)
    _, ran = proposal(context, run_code, value)
    asyncio.run(run_code(trusted, value))
    own = replace(context["caller"], origin="http")
    grants = list_notebook_grants(own, GrantQuery(conversation_id=context["conversation"]))
    assert {g.approval_id for g in grants} == {edited, ran}

    later = EditNotebook(path=edit.path, action="insert", index=1, source="y")
    assert asyncio.run(edit_granted_notebook(trusted, later)).cells == 2
    run = GrantedRun(notebook=edit.path, code="y")
    assert asyncio.run(run_in_granted_notebook(trusted, run)).status == "ok"
    # A run grant alone covers no edit of its notebook.
    proposal(context, run_code, RunCode(code="x", notebook="work/run.ipynb"))
    with pytest.raises(ApprovalRequired):
        asyncio.run(
            edit_granted_notebook(trusted, msgspec.structs.replace(later, path="work/run.ipynb"))
        )
    assert [call[0] for call in calls] == ["notebook", "run", "notebook", "run"]


def test_a_rejected_edit_grants_nothing(context, workspace):
    from app.workspace.handlers import edit_granted_notebook

    value = EditNotebook(path="work/no.ipynb", action="insert", index=0, source="x")
    trusted, _ = proposal(context, edit_notebook, value, approve=False)
    with pytest.raises(ApprovalRequired):
        asyncio.run(edit_granted_notebook(trusted, value))


def test_kernel_outputs_keep_two_images_and_the_last_traceback_lines():
    import base64

    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 10).decode()
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4">'
        '<rect width="4" height="4"/></svg>'
    )
    outputs = workspaces.KernelOutputs()
    outputs.add("display_data", {"data": {"image/png": png, "text/plain": "<Figure>"}})
    outputs.add("display_data", {"data": {"image/svg+xml": svg}})
    outputs.add("display_data", {"data": {"image/png": png}})
    trace = ["\x1b[31mTraceback\x1b[0m"] + [f"line {n}" for n in range(30)]
    outputs.add("error", {"ename": "ValueError", "evalue": "bad", "traceback": ["\n".join(trace)]})
    result = asyncio.run(outputs.result())
    assert [image["media_type"] for image in result["images"]] == ["image/png", "image/png"]
    assert base64.b64decode(result["images"][1]["data_base64"]).startswith(b"\x89PNG")
    assert result["truncated"] and result["status"] == "error"
    assert result["error"] == "ValueError: bad"
    assert result["traceback"] == [f"line {n}" for n in range(10, 30)]
    assert "<Figure>" not in "".join(result["outputs"])


def test_svg_outputs_render_after_the_receive_loop_in_a_worker_thread(monkeypatch):
    import threading

    import vl_convert

    rendered = []
    original = vl_convert.svg_to_png

    def svg_to_png(svg):
        rendered.append(threading.current_thread() is threading.main_thread())
        return original(svg)

    monkeypatch.setattr(vl_convert, "svg_to_png", svg_to_png)
    small = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4">'
        '<rect width="4" height="4"/></svg>'
    )
    large = small.replace("</svg>", " " * workspaces.SVG_LIMIT + "</svg>")
    outputs = workspaces.KernelOutputs()
    outputs.add("display_data", {"data": {"image/svg+xml": large}})
    outputs.add("display_data", {"data": {"image/svg+xml": small}})
    assert not rendered  # nothing is rendered while outputs arrive
    result = asyncio.run(outputs.result())
    assert rendered == [False]  # the small one, once, in a worker thread
    assert [image["media_type"] for image in result["images"]] == ["image/png"]
    assert result["truncated"]


@pytest.mark.parametrize("kernel", ["running", "queued", "unreachable", "refused"])
def test_code_past_its_time_answers_run_timeout_and_only_its_own_run_is_interrupted(
    monkeypatch, kernel
):
    import json
    from contextlib import asynccontextmanager

    import websockets.asyncio.client

    from app.infrastructure.errors import RunTimeout, WorkspaceUnavailable

    posted = []

    class Channel:
        """The kernel never finishes: it starts the request, or leaves it queued behind a cell."""

        def __init__(self):
            self.replies = asyncio.Queue()

        async def send(self, payload):
            if kernel == "running":
                header = json.loads(payload)["header"]
                busy = {"header": {"msg_type": "status"}, "content": {"execution_state": "busy"}}
                self.replies.put_nowait(json.dumps(busy | {"parent_header": header}))

        async def recv(self):
            return await self.replies.get()

    @asynccontextmanager
    async def open_channel(url, **kwargs):
        if kernel == "unreachable":
            await asyncio.Event().wait()  # the handshake never completes
        if kernel == "refused":
            raise ConnectionRefusedError("generated")
        yield Channel()

    def jupyter(request):
        posted.append((request.method, request.url.path))
        return httpx.Response(204)

    original = hub.client

    @asynccontextmanager
    async def client(base, token, timeout=30):
        async with original(base, token, timeout) as http:
            http._transport = httpx.MockTransport(jupyter)
            yield http

    monkeypatch.setattr(websockets.asyncio.client, "connect", open_channel)
    monkeypatch.setattr(hub, "client", client)
    target = workspaces.JupyterTarget("http://jupyter/user/x/", "generated-never-sent", KERNEL)
    error = WorkspaceUnavailable if kernel in {"unreachable", "refused"} else RunTimeout
    with pytest.raises(error):
        asyncio.run(workspaces.run_in_kernel(target, "while True: pass", seconds=0.1))
    interrupt = [("POST", f"/user/x/api/kernels/{KERNEL}/interrupt")]
    assert posted == (interrupt if kernel == "running" else [])
    assert RunTimeout.status == 504 and RunTimeout.code == "run_timeout"
    assert workspaces.RUN_SECONDS < 120  # the agent's MCP request timeout


def test_kernel_images_reach_the_model_as_image_content():
    from mcp.types import CallToolResult, TextContent

    from app.infrastructure.mcp.client import client

    results = []
    tool = client("http://127.0.0.1:1/mcp", "generated", results, allowed=["run_code"])
    value = {
        "status": "ok",
        "outputs": [],
        "images": [{"mediaType": "image/png", "dataBase64": "iVBORw0KGgo="}],
    }
    assert tool.parse_tool_results is not None
    parsed = tool.parse_tool_results(
        CallToolResult(content=[TextContent(type="text", text="{}")], structuredContent=value)
    )
    assert not isinstance(parsed, str)
    assert [item.type for item in parsed] == ["text", "data"]
    assert parsed[1].media_type == "image/png"
    assert results == [{"status": "ok", "outputs": []}]
