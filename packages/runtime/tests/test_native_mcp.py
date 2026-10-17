"""Native model/tool protocol against a synthetic authenticated loopback server."""

import asyncio
import hashlib
import json
import socket
import threading
import time
from contextlib import asynccontextmanager, contextmanager

import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import CallToolResult, TextContent, Tool
from runtime_test_support import QUOTE, collect, fixture
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Mount

from craft_runtime.native_mcp import NativeMCPSource
from craft_runtime.runner import Binding
from craft_runtime.state import RunRequest


@contextmanager
def synthetic_server(tokens):
    server = Server("synthetic-tools")

    @server.list_tools()
    async def tools():
        return [
            Tool(
                name="lookup",
                description="Read generated evidence.",
                inputSchema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            )
        ]

    @server.call_tool(validate_input=False)
    async def call(name, arguments):
        value = {
            "text": QUOTE,
            "handle": {
                "item": "generated-document",
                "version": 1,
                "block": 1,
                "quoteSha256": hashlib.sha256(QUOTE.encode()).hexdigest(),
            },
        }
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(value))], structuredContent=value
        )

    manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True)

    async def transport(scope, receive, send):
        header = dict(scope["headers"]).get(b"authorization", b"").decode()
        if not header.startswith("Bearer synthetic-credential-"):
            await Response(status_code=401)(scope, receive, send)
            return
        tokens.append(header)
        await manager.handle_request(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield

    app = Starlette(routes=[Mount("/mcp", app=transport)], lifespan=lifespan)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    host, port = listener.getsockname()
    config = uvicorn.Config(app, log_level="error", access_log=False)
    process = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: process.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if process.started:
                break
            time.sleep(0.01)
        assert process.started
        yield f"http://{host}:{port}/mcp/"
    finally:
        process.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


def test_native_http_tools_share_runner_identity_and_close():
    received = []
    with synthetic_server(received) as url:

        async def run():
            runtime, store, _, credentials, models, _ = fixture()
            sources = []

            def source(request, token):
                result = NativeMCPSource(url, token, {"lookup": Binding("generated-evidence")})
                sources.append(result)
                return result

            runtime.tools_factory = source
            request = RunRequest("owner", "conversation", "native-http", "Read generated evidence")
            events = await collect(runtime, request)
            assert events[-1].kind == "finished" and events[-1].data["citations"]
            assert store.runs[request.run].status == "finished"
            assert received and set(received) == {"Bearer " + credentials.issued[0]}
            assert models[0].closed and sources[0].http.is_closed and not credentials.active

        asyncio.run(run())
