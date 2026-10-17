"""An owned native loopback MCP source; permissions remain at the server."""

import json
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import urlsplit

import httpx
from agent_framework import MCPStreamableHTTPTool
from agent_framework.exceptions import ToolExecutionException

from craft_runtime.runner import Binding
from craft_runtime.state import RuntimeErrorCode


class SingleAttemptMCP(MCPStreamableHTTPTool):
    """The pinned SDK reconnect hook must not replay a possibly accepted call."""

    async def _call_tool_with_retries(self, tool_name, filtered_kwargs, meta, parser, span):
        try:
            session = self.session
            if session is None:
                raise RuntimeErrorCode("tool_disconnected")
            result = await session.call_tool(tool_name, arguments=filtered_kwargs, meta=meta)
            return parser(result)
        except Exception:
            raise ToolExecutionException("tool_failed") from None


class NativeMCPSource:
    def __init__(self, url: str, token: str, bindings: dict[str, Binding]):
        parts = urlsplit(url)
        if (
            parts.scheme != "http"
            or parts.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parts.path.rstrip("/") != "/mcp"
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise RuntimeErrorCode("invalid_mcp_url")
        self.bindings = {name: binding for name, binding in bindings.items() if name != "ask_agent"}
        self.http = httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=120,
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        )
        self.tool = SingleAttemptMCP(
            name="local-tools",
            url=url,
            allowed_tools=list(self.bindings),
            http_client=self.http,
            header_provider=lambda _: {"Authorization": "Bearer " + token},
            approval_mode="never_require",
            load_prompts=False,
            parse_tool_results=self.parse,
            request_timeout=120,
        )

    @staticmethod
    def parse(result):
        if result.isError:
            raise RuntimeErrorCode("tool_failed")
        value = result.structuredContent
        if value is None:
            value = next(
                (json.loads(content.text) for content in result.content if content.type == "text"),
                None,
            )
        return json.dumps(value, allow_nan=False)

    @property
    def functions(self):
        return self.tool.functions

    async def connect(self):
        await self.tool.connect()
        for function in self.tool.functions:
            if self.bindings[function.name].effectful:
                function.approval_mode = "always_require"

    async def close(self):
        try:
            await self.tool.close()
        finally:
            await self.http.aclose()
