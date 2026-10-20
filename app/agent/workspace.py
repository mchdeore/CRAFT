"""Optional private external toolsets, resolved only from the trusted server caller.

No CRAFT route, registry entry or public tool schema is added here. The run
controller owns connection/cleanup and must handle external tool middleware
separately from the CRAFT-only ToolCallMiddleware.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from urllib.parse import urlsplit

from app.infrastructure.access import Caller
from app.infrastructure.mcp.client import LocalHTTPTool


@dataclass(frozen=True)
class WorkspaceEndpoint:
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    tools: tuple[str, ...]

    def __post_init__(self):
        target = urlsplit(self.url)
        if (
            target.scheme not in {"http", "https"}
            or not target.hostname
            or target.username is not None
            or target.password is not None
            or target.query
            or target.fragment
            or not self.tools
        ):
            raise ValueError("Expected a private configured endpoint and explicit tool allowlist")
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


class WorkspaceToolsets:
    """Root-owned owner map. No endpoint or owner is selected by model arguments."""

    def __init__(self, endpoints: Mapping[str, WorkspaceEndpoint]):
        self._endpoints = dict(endpoints)

    def for_caller(self, caller: Caller) -> LocalHTTPTool:
        endpoint = self._endpoints.get(str(caller.user_id))
        if endpoint is None or caller.user_id is None:
            raise PermissionError("No private workspace is configured for this owner")
        return LocalHTTPTool(
            name="user_workspace",
            url=endpoint.url,
            tool_name_prefix="workspace_",
            allowed_tools=endpoint.tools,
            approval_mode="never_require",
            load_prompts=False,
            header_provider=lambda _: dict(endpoint.headers),
            request_timeout=120,
        )
