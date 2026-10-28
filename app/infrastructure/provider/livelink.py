"""Originals through a LiveLink-shaped REST API (``/api/v2/nodes``); it never scans or indexes.

Item and version records stay the provider's own, the ones the frozen index was built
from; only the original bytes come over the API. A node's ID is its item's key read as a
number (``itm_00000000002a`` is node 42), so switching the setting changes nothing else.
"""

import hashlib
from pathlib import Path
from typing import Any

import httpx

from app.infrastructure.provider.simulated import SimulatedProvider


def node_id(item: str) -> int:
    if not item.startswith("itm_") or len(item) != 16:
        raise KeyError(item)
    return int(item[4:], 16)


def item_key(node: int) -> str:
    return f"itm_{node:012x}"


class LiveLinkProvider:
    def __init__(
        self,
        root: str | Path,
        url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.records = SimulatedProvider(root)
        self.http = httpx.Client(
            base_url=url.rstrip("/") + "/api/v2/",
            headers={"OTCSTicket": token},
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=60,
        )

    def scan(self) -> list[dict[str, Any]]:
        raise RuntimeError("The LiveLink adapter reads originals only; the index stays frozen")

    def items(self) -> dict[str, dict[str, Any]]:
        return self.records.items()

    def list_changes(self, cursor: int) -> tuple[list[dict[str, Any]], int]:
        return self.records.list_changes(cursor)

    def get_item(self, item: str) -> dict[str, Any]:
        return self.records.get_item(item)

    def read_content(self, item: str, version: int, *, max_bytes: int | None = None) -> bytes:
        entry = next((v for v in self.get_item(item)["versions"] if v["version"] == version), None)
        if entry is None:
            raise KeyError("unknown version")
        address = f"nodes/{node_id(item)}/versions/{version}/content"
        try:
            with self.http.stream("GET", address) as response:
                if response.status_code in {403, 404}:
                    raise KeyError("unreadable version")
                response.raise_for_status()
                content = b""
                for chunk in response.iter_bytes():
                    content += chunk
                    if max_bytes is not None and len(content) > max_bytes:
                        raise ValueError("Source exceeds configured byte limit.")
        except httpx.HTTPError:
            raise FileNotFoundError(item) from None
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise KeyError("changed version")  # versions are immutable; changed bytes withdraw it
        return content
