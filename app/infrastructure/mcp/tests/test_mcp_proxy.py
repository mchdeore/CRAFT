"""Native local MCP traffic must stay direct and ignore response cookies."""

import asyncio
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.infrastructure.mcp.client import client

TOKEN = "synthetic-run-credential"
QUERY = "Synthetic engineering query"


@contextmanager
def synthetic_server():
    observations = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            method = body["method"]
            observations.append(
                {
                    "method": method,
                    "credential_matches": self.headers.get("Authorization") == "Bearer " + TOKEN,
                    "cookie_present": "Cookie" in self.headers,
                    "query_matches": body.get("params", {}).get("arguments", {}).get("query")
                    == QUERY,
                }
            )
            if method.startswith("notifications/"):
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if method == "initialize":
                result = {
                    "protocolVersion": body["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "synthetic", "version": "1"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "search",
                            "description": "Synthetic local lookup",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"],
                            },
                        }
                    ]
                }
            else:
                result = {
                    "content": [{"type": "text", "text": '{"value":4}'}],
                    "structuredContent": {"value": 4},
                }
            encoded = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Set-Cookie", "synthetic_cookie=marker; Path=/")
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp", observations
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


def test_native_mcp_ignores_environment_proxies_and_response_cookies(monkeypatch):
    with synthetic_server() as (url, direct), synthetic_server() as (proxy_url, proxy):
        for name in (
            "HTTP_PROXY",
            "http_proxy",
            "HTTPS_PROXY",
            "https_proxy",
            "ALL_PROXY",
            "all_proxy",
        ):
            monkeypatch.setenv(name, proxy_url.removesuffix("/mcp"))
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")

        async def execute():
            transport = client(url, TOKEN, [], allowed=["search"])
            try:
                await transport.connect()
                await transport.call_tool("search", query=QUERY)
            finally:
                await transport.close()

        asyncio.run(asyncio.wait_for(execute(), timeout=10))
        proxied = len(proxy)
        assert proxied == 0
        calls = [row for row in direct if row["method"] == "tools/call"]
        assert len(calls) == 1
        authenticated = all(row["credential_matches"] for row in direct)
        correct_query = calls[0]["query_matches"]
        cookies_absent = all(not row["cookie_present"] for row in direct)
        assert authenticated and correct_query and cookies_absent
