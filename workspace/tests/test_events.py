"""Workspace activity lines: metadata only, joined to CRAFT by user ID."""

import asyncio
import json
import secrets

import craft_events

USER = "01a184ed-792a-7a34-bd65-b27d3dc77cc4"


def lines(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_a_save_logs_path_and_size_but_never_the_content(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JUPYTERHUB_USER", USER)
    monkeypatch.setenv("USER_ROOT", str(tmp_path))
    content = "generated-" + secrets.token_hex(8)
    (tmp_path / "data.txt").write_text(content)
    for action in ("get", "save"):
        asyncio.run(
            craft_events.on_file(
                None, craft_events.CONTENTS, {"action": action, "path": "data.txt"}
            )
        )
    (line,) = lines(capsys)
    assert line["kind"] == "file.save" and line["path"] == "data.txt"
    assert line["bytes"] == len(content) and line["user_id"] == USER
    assert content not in json.dumps(line)


def test_a_cell_run_logs_count_timing_and_outcome_but_never_code_or_output(monkeypatch, capsys):
    monkeypatch.setenv("JUPYTERHUB_USER", USER)
    secret = "generated-" + secrets.token_hex(8)

    async def path_of(kernel_id):
        return "work/fit.ipynb"

    def message(kind, request, **content):
        return {
            "header": {"msg_type": kind},
            "parent_header": {"msg_type": request[0], "msg_id": request[1]},
            "content": content,
        }

    async def run():
        cells = craft_events.Cells("k1", path_of)
        cell, silent = ("execute_request", "a"), ("execute_request", "b")
        for msg in [
            message("status", cell, execution_state="busy"),
            message("execute_input", cell, code=f"x = {secret!r}", execution_count=3),
            message("stream", cell, name="stdout", text=secret),
            message("error", cell, ename="ValueError", evalue=secret, traceback=[secret]),
            message("status", cell, execution_state="idle"),
            # Silent requests echo no input and aren't logged.
            message("status", silent, execution_state="busy"),
            message("status", silent, execution_state="idle"),
            message("status", ("complete_request", "c"), execution_state="busy"),
        ]:
            cells.message(msg)
        await asyncio.sleep(0)

    asyncio.run(run())
    (line,) = lines(capsys)
    assert line["kind"] == "cell.execute" and line["path"] == "work/fit.ipynb"
    assert line["execution_count"] == 3 and line["outcome"] == "error"
    assert line["user_id"] == USER and isinstance(line["duration_ms"], int)
    assert secret not in json.dumps(line)


def test_a_cell_run_parses_only_its_status_and_input_and_holds_its_write(monkeypatch, capsys):
    from jupyter_client.session import Session

    monkeypatch.setenv("JUPYTERHUB_USER", USER)
    session = Session(key=b"generated-key")
    request = session.msg_header("execute_request")

    def frames(kind, content, raw=None):
        parts = session.serialize(session.msg(kind, content=content, parent=request))
        if raw is not None:  # an output's content, signed like any other but never parsed
            parts[-1] = raw
            parts[1] = session.sign(parts[2:])
        return parts

    async def path_of(kernel_id):
        return "work/fit.ipynb"

    async def run():
        cells = craft_events.Cells("k1", path_of)
        for parts in [
            frames("status", {"execution_state": "busy"}),
            frames("execute_input", {"code": "x = 1", "execution_count": 7}),
            frames("display_data", {}, raw=b"\xff not JSON"),
            frames("status", {"execution_state": "idle"}),
        ]:
            cells.feed(session, parts)
        (write,) = cells.writes  # the loop alone holds only a weak reference
        await write
        await asyncio.sleep(0)
        assert not cells.writes

    asyncio.run(run())
    (line,) = lines(capsys)
    assert line["kind"] == "cell.execute" and line["execution_count"] == 7
    assert line["outcome"] == "ok"


def test_lines_use_crafts_log_shape(capsys):
    craft_events.write("kernel.start", kernel_id="k1", outcome="success")
    (line,) = lines(capsys)
    assert list(line)[:4] == ["at", "level", "logger", "event"]
    assert line["logger"] == "craft.workspace" and line["origin"] == "workspace"
