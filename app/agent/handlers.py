"""Private typed model infrastructure entry points for trusted source processing."""

import asyncio
from typing import Annotated, Literal

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


class ModelMessage(Shape):
    role: Literal["system", "user", "assistant", "tool"]
    text: str


class ModelInput(Shape):
    messages: Annotated[list[ModelMessage], msgspec.Meta(min_length=1)]
    role: Literal["main", "check"] = "main"


class ModelOutput(Shape):
    text: str
    model: str


@operation(PUBLIC)
async def respond(caller: Caller, request: ModelInput) -> ModelOutput:
    """Call the selected model with already authorized input through a private typed entry."""
    if caller.settings is None:
        from app.infrastructure.errors import NotFound

        raise NotFound()
    text, selected = await model.request(caller.settings, request.messages, request.role)
    return ModelOutput(text=text, model=selected)


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
