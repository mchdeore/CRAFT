"""Private typed model infrastructure entry points for trusted source processing."""

import asyncio
from typing import Annotated

import msgspec

from app.agent import model
from app.infrastructure.access import PUBLIC, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape


class EmbeddingInput(Shape):
    text: str


class EmbeddingOutput(Shape):
    vector: Annotated[list[float], msgspec.Meta(min_length=1536, max_length=1536)]
    model: str


@operation(PUBLIC)
def embed(caller: Caller, request: EmbeddingInput) -> EmbeddingOutput:
    """Embed already authorized source text through a Python-only infrastructure entry point."""
    if caller.settings is None:
        from app.infrastructure.errors import NotFound

        raise NotFound()
    return EmbeddingOutput(
        vector=asyncio.run(model.embedding(caller.settings, request.text)),
        model=(
            "fake-model-embed@1"
            if caller.settings.model_main == "fake"
            else caller.settings.model_embedding
        ),
    )
