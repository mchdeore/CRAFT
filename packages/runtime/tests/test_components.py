"""Generated local components through the application registry and native MCP."""

import asyncio
import hashlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
import uvicorn
from agent_framework import ChatResponse, Content, Message
from agent_framework.exceptions import ToolExecutionException
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from runtime_test_support import ScriptedClient, collect
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Mount

from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.mcp.server import build_server
from app.infrastructure.operations import DATA, operation
from app.infrastructure.operations.registry import registry
from app.infrastructure.shapes import Shape
from craft_data import (
    BlockInput,
    CubeQuery,
    CubeValueInput,
    DocumentStore,
    SearchQuery,
    StorageMode,
    ValueState,
    insert_cube_value,
    query_cube,
    search,
)
from craft_runtime.decisions import MemoryDecisionGate
from craft_runtime.registry import source_from_registry
from craft_runtime.runner import Runtime
from craft_runtime.sqlite_store import SQLiteRunStore
from craft_runtime.state import RunRequest
from craft_workspace.manager import WorkspaceManager

NOW = datetime(2025, 1, 1, tzinfo=UTC)
QUOTE = "Generated mass is 12 kg."
OWNER = str(uuid4())
FOREIGN = str(uuid4())
credential: ContextVar[Caller] = ContextVar("component_fixture_caller")


class ReadInput(Shape):
    query: str


class Evidence(Shape):
    text: str
    total: int
    handle: dict[str, Any] | None


class MetricInput(Shape):
    metric: str


class MetricValue(Shape):
    state: str
    amount: str | None
    unit: str | None
    source: dict[str, Any] | None


class PythonInput(Shape):
    code: str


class PythonValue(Shape):
    text: str


class Credentials:
    def __init__(self):
        self.active: dict[str, RunRequest] = {}
        self.issued: list[str] = []

    async def issue(self, request: RunRequest) -> str:
        token = uuid4().hex
        self.active[token] = request
        self.issued.append(token)
        return token

    async def revoke(self, request: RunRequest) -> None:
        self.active = {
            token: value for token, value in self.active.items() if value.run != request.run
        }


@contextmanager
def component_server(
    store: DocumentStore, workspace: WorkspaceManager, session: str, credentials: Credentials
) -> Iterator[str]:
    snapshot = registry.copy()
    writes: list[bool] = []
    try:

        @operation(SIGNED_IN, get="/component-lookup", tool=True)
        def lookup(caller: Caller, request: ReadInput) -> Evidence:
            """Read generated local evidence using trusted caller identity."""
            page = search(store, SearchQuery(request.query, str(caller.user_id)))
            if not page.hits:
                return Evidence(text="", total=page.total_hits, handle=None)
            hit = page.hits[0]
            return Evidence(
                text=hit.excerpt,
                total=page.total_hits,
                handle={
                    "item": hit.citation.item_id,
                    "version": hit.citation.version,
                    "block": hit.citation.block_id,
                    "location": hit.citation.location,
                    "contentHash": hit.citation.content_hash,
                    "quoteSha256": hashlib.sha256(hit.excerpt.encode()).hexdigest(),
                },
            )

        @operation(SIGNED_IN, get="/component-cube", tool=True)
        def read_metric(caller: Caller, request: MetricInput) -> MetricValue:
            """Read an exact generated value from the permitted cube."""
            value = query_cube(
                store,
                CubeQuery(
                    str(caller.user_id), "mission", request.metric, NOW, NOW, target_unit="g"
                ),
            )
            row = value.rows[0] if value.rows else None
            return MetricValue(
                state=row.state.value if row else "withheld",
                amount=str(row.amount) if row and row.amount is not None else None,
                unit=row.unit if row else None,
                source=asdict(row.source) if row and row.source else None,
            )

        @operation(SIGNED_IN, post="/component-python", tool=True)
        def run_python(caller: Caller, request: PythonInput) -> PythonValue:
            """Execute generated local scratchpad code in an owned isolated fixture."""
            if str(caller.user_id) != OWNER:
                from app.infrastructure.errors import Forbidden

                raise Forbidden()
            result = workspace.execute(session, request.code, timeout=5)
            assert not result.error and not result.timed_out
            return PythonValue(text=result.stdout.strip())

        @operation(SIGNED_IN, post="/component-write", tool=True, effects=DATA)
        def forbidden_write(caller: Caller, request: MetricInput) -> None:
            """Attempt an unapproved fixture write."""
            writes.append(True)

        async def caller():
            return credential.get(), None

        server = build_server(registry, caller)
        manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True)

        async def transport(scope, receive, send):
            raw = dict(scope["headers"]).get(b"authorization", b"").decode().removeprefix("Bearer ")
            request = credentials.active.get(raw)
            if request is None:
                await Response(status_code=401)(scope, receive, send)
                return
            token = credential.set(
                Caller(
                    user_id=UUID(request.owner),
                    conversation_id=UUID(request.conversation),
                    run_id=UUID(request.run),
                    origin="mcp",
                )
            )
            try:
                await manager.handle_request(scope, receive, send)
            finally:
                credential.reset(token)

        @asynccontextmanager
        async def lifespan(app):
            async with manager.run():
                yield

        app = Starlette(routes=[Mount("/mcp", app=transport)], lifespan=lifespan)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        host, port = listener.getsockname()
        process = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        thread = threading.Thread(target=lambda: process.run(sockets=[listener]), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not process.started and time.monotonic() < deadline:
                time.sleep(0.01)
            assert process.started
            yield f"http://{host}:{port}/mcp"
        finally:
            process.should_exit = True
            thread.join(5)
            listener.close()
            assert not thread.is_alive() and not writes
    finally:
        registry.clear()
        registry.update(snapshot)


class ComponentModel(ScriptedClient):
    def reply(self, messages):
        results = [c for m in messages for c in m.contents if c.type == "function_result"]
        if results and not json.loads(results[0].result)["text"]:
            contents = [Content.from_text("No accessible evidence.")]
        elif len(results) == 0:
            contents = [
                Content.from_function_call(
                    call_id="read", name="lookup", arguments={"query": "mass"}
                )
            ]
        elif len(results) == 1:
            contents = [
                Content.from_function_call(
                    call_id="cube", name="read_metric", arguments={"metric": "mass"}
                )
            ]
        elif len(results) == 2:
            value = json.loads(results[1].result)
            assert value["source"]["block_id"] == json.loads(results[0].result)["handle"]["block"]
            amount = value["amount"]
            contents = [
                Content.from_function_call(
                    call_id="python",
                    name="run_python",
                    arguments={
                        "code": f"from decimal import Decimal\nprint(Decimal({amount!r}) / 1000)"
                    },
                )
            ]
        else:
            contents = [
                Content.from_text(
                    QUOTE + " Calculated mass is " + json.loads(results[-1].result)["text"] + " kg."
                )
            ]
        return ChatResponse(
            messages=[Message(role="assistant", contents=contents, message_id=f"m{len(results)}")],
            response_id=f"r{len(results)}",
            model="scripted",
        )


@pytest.mark.docker
def test_generated_data_cube_kernel_flow_uses_registry_and_native_mcp(tmp_path):
    store = DocumentStore(
        tmp_path / "documents", mode=StorageMode.LOCAL_ORIGINALS, clock=lambda: NOW
    )
    document = store.create_local_document(
        name="fixture",
        source_location="fixture.txt",
        mission="mission",
        classification="generated",
        synthetic=True,
    )
    version = store.ingest_local(
        document.document_id,
        QUOTE.encode(),
        (BlockInput(QUOTE, "line:1"),),
        extraction_method="fixture",
    )
    block = store.get_blocks(version.version_id)[0]
    store.set_mission_permission(principal=OWNER, mission="mission", can_read=True)
    insert_cube_value(
        store,
        CubeValueInput(
            "mission",
            "mass",
            "unit-1",
            ValueState.VALUE,
            Decimal(12),
            "kg",
            NOW,
            NOW,
            version.version_id,
            block.block_id,
            block.location,
            block.text_hash,
        ),
    )
    workspace = WorkspaceManager()
    session = workspace.open()
    credentials = Credentials()
    try:
        with component_server(store, workspace, session, credentials) as url:

            async def run():
                saved = SQLiteRunStore(tmp_path / "runs.sqlite")
                models = []
                sources = []

                def model():
                    value = ComponentModel()
                    models.append(value)
                    return value

                def tools(request, token):
                    value = source_from_registry(url, token, registry.values())
                    sources.append(value)
                    return value

                runtime = Runtime(
                    store=saved,
                    decisions=MemoryDecisionGate(),
                    credentials=credentials,
                    model_factory=model,
                    tools_factory=tools,
                    seconds=90,
                )
                request = RunRequest(
                    OWNER,
                    str(uuid4()),
                    str(uuid4()),
                    "Read the mass, convert it and calculate in Python.",
                )
                events = await collect(runtime, request)
                assert (
                    events[-1].kind == "finished"
                    and events[-1].data["citations"][0]["text"] == QUOTE
                )
                assert "forbidden_write" not in models[0].offered
                assert not credentials.active and models[0].closed and sources[0].http.is_closed
                denied = await collect(
                    runtime, RunRequest(FOREIGN, str(uuid4()), str(uuid4()), "Read mass.")
                )
                assert denied[-1].kind == "finished" and denied[-1].data["citations"] == []
                assert sources[-1].http.is_closed and not credentials.active
                direct_request = RunRequest(FOREIGN, str(uuid4()), str(uuid4()), "Probe refusal")
                raw = await credentials.issue(direct_request)
                guarded = source_from_registry(url, raw, registry.values())
                try:
                    await guarded.connect()
                    write = next(f for f in guarded.functions if f.name == "run_python")
                    with pytest.raises(ToolExecutionException):
                        await write.invoke(arguments={"code": "print('must not execute')"})
                    native_session = guarded.tool.session
                    assert native_session is not None
                    result = await native_session.call_tool(
                        "lookup", arguments={"query": "mass", "principal": OWNER}
                    )
                    assert result.isError
                    assert result.structuredContent is not None
                    assert result.structuredContent["code"] == "invalid_input"
                finally:
                    await guarded.close()
                    await credentials.revoke(direct_request)
                assert guarded.http.is_closed and not credentials.active

            asyncio.run(run())
    finally:
        bundle = workspace.close(session)
        assert workspace.close(session) == bundle and not workspace.sessions
