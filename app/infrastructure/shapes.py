"""The msgspec Shape base: strict, camelCase on the wire, unknown fields refused."""

from typing import Annotated, Any, ClassVar, Literal, dataclass_transform

import msgspec
from starlette.responses import Response


@dataclass_transform(frozen_default=True, kw_only_default=True, field_specifiers=(msgspec.field,))
class Shape(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True, rename="camel"):
    """Base of every input and output. The decorator tells type checkers that subclasses
    inherit frozen and keyword-only, as msgspec does at runtime."""


class RawFile(Shape):
    max_bytes: ClassVar[int] = 25 * 1024 * 1024
    content: Annotated[bytes, msgspec.Meta(max_length=25 * 1024 * 1024)]
    file_name: Annotated[str, msgspec.Meta(min_length=1, max_length=255)]
    media_type: Annotated[str, msgspec.Meta(min_length=1, max_length=255)]
    simulated: bool = False


class Health(Shape):
    status: Literal["ok", "unavailable"]


class FieldError(Shape):
    path: str
    message: str


class ApiErrorBody(Shape, omit_defaults=True):
    code: str
    message: str
    request_id: str | None
    fields: list[FieldError] | None = None


def json_response(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    """Encode with msgspec; the only JSON encoder for responses."""
    return Response(msgspec.json.encode(body), status, headers, media_type="application/json")
