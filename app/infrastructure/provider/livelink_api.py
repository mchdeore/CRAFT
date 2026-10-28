"""A LiveLink-shaped API over the simulated provider's originals (``craft provider serve``).

It answers the ``/api/v2/nodes`` paths the LiveLink adapter reads: a document node, its
versions and a version's bytes. Requests need the ``OTCSTicket`` header. Nodes listed in
``denied`` answer 403, as a node the service account may not read would.
"""

import hmac
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.infrastructure.provider.livelink import item_key
from app.infrastructure.provider.simulated import SimulatedProvider

DOCUMENT = 144


def livelink_api(root: str | Path, ticket: str, denied: set[int] | None = None) -> Starlette:
    provider = SimulatedProvider(root)
    blocked = denied if denied is not None else set()

    def record(request: Request) -> dict[str, Any] | Response:
        """The node's provider record, or the refusal the API answers with."""
        if not ticket or not hmac.compare_digest(request.headers.get("OTCSTicket", ""), ticket):
            return Response(status_code=401)
        node = int(request.path_params["node"])
        if node in blocked:
            return Response(status_code=403)
        try:
            found = provider.get_item(item_key(node))
        except KeyError:
            return Response(status_code=404)
        return found if found["deleted_at"] is None else Response(status_code=404)

    def node(request: Request) -> Response:
        found = record(request)
        if isinstance(found, Response):
            return found
        properties = {
            "id": int(request.path_params["node"]),
            "name": Path(found["path"]).name,
            "type": DOCUMENT,
        }
        return JSONResponse({"results": {"data": {"properties": properties}}})

    def versions(request: Request) -> Response:
        found = record(request)
        if isinstance(found, Response):
            return found
        rows = [
            {
                "data": {
                    "version_number": v["version"],
                    "file_name": v["file_name"],
                    "mime_type": v["media_type"],
                }
            }
            for v in found["versions"]
        ]
        return JSONResponse({"results": rows})

    def content(request: Request) -> Response:
        found = record(request)
        if isinstance(found, Response):
            return found
        try:
            data = provider.read_content(found["item"], int(request.path_params["version"]))
        except (KeyError, FileNotFoundError):
            return Response(status_code=404)
        return Response(data, media_type="application/octet-stream")

    return Starlette(
        routes=[
            Route("/api/v2/nodes/{node:int}", node),
            Route("/api/v2/nodes/{node:int}/versions", versions),
            Route("/api/v2/nodes/{node:int}/versions/{version:int}/content", content),
        ]
    )
