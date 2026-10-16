"""Private typed model infrastructure entry points for trusted source processing."""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any, ClassVar, Literal
from uuid import UUID

import msgspec

from app.agent import model
from app.infrastructure.access import PUBLIC, SIGNED_IN, Caller
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


class RunAgentInput(Shape, forbid_unknown_fields=False):
    max_bytes: ClassVar[int] = 1024 * 1024
    thread_id: UUID
    run_id: UUID
    messages: list[dict[str, Any]]
    resume: Any = None


class AGUIEvent(Shape, omit_defaults=True):
    type: str
    thread_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None
    tool_call_id: str | None = None
    parent_message_id: str | None = None
    tool_call_name: str | None = None
    role: str | None = None
    delta: Any = None
    snapshot: Any = None
    messages: Any = None
    state: Any = None
    activity_type: str | None = None
    content: Any = None
    replace: bool | None = None
    outcome: Any = None
    code: str | None = None
    message: str | None = None
    name: str | None = None
    value: Any = None
    timestamp: Any = None
    raw_event: Any = None


class ToolInfo(Shape):
    name: str
    operation: str
    description: str
    effects: str
    needs_approval: bool
    route: str


class RunRef(Shape):
    run: UUID


class RunTokenDigest(Shape):
    digest: Annotated[bytes, msgspec.Meta(min_length=32, max_length=32)]


class RunToken(Shape):
    token: str


class RunPrincipal(Shape):
    user: UUID
    conversation: UUID
    run: UUID
    roles: list[str]
    request_id: UUID | None = None
    parent_call_id: UUID | None = None
    auth_session_id: UUID | None = None


@operation(SIGNED_IN)
def issue_run_token(caller: Caller, request: RunRef) -> RunToken:
    """Issue an ephemeral credential for an owned active run through a private entry."""
    from app.agent import service

    return RunToken(token=service.issue_token(caller, request.run))


@operation(SIGNED_IN)
def revoke_run_token(caller: Caller, request: RunRef) -> None:
    """Revoke an owned run credential through a private entry."""
    from app.agent import service

    service.revoke_token(caller, request.run)


@operation(PUBLIC)
def resolve_run_token(caller: Caller, request: RunTokenDigest) -> RunPrincipal:
    """Resolve a digest into a current run principal through a private entry."""
    from dataclasses import replace

    from app.agent import service
    from app.infrastructure.operations.context import current_call

    principal = RunPrincipal(**service.principal(caller, request.digest))
    context = current_call.get()
    if context is not None:
        context.caller = replace(
            caller,
            user_id=principal.user,
            roles=frozenset(principal.roles),
            conversation_id=principal.conversation,
            run_id=principal.run,
            auth_session_id=principal.auth_session_id,
        )
    return principal


@operation(SIGNED_IN, get="/tools")
def list_tools(caller: Caller) -> list[ToolInfo]:
    """List generated tool names and effects; for example readable library search."""
    import inspect

    from app.infrastructure.operations.registry import registry

    return [
        ToolInfo(
            name=o.tool_name,
            operation=o.name,
            description=inspect.getdoc(o.function) or "",
            effects=o.effects,
            needs_approval=o.effects != "NONE",
            route=str(o.method) + " /api" + str(o.path),
        )
        for o in registry.values()
        if o.tool
    ]


@operation(SIGNED_IN, post="/agent/runs")
async def run_agent(caller: Caller, request: RunAgentInput) -> AsyncIterator[AGUIEvent]:
    """Stream an owned run with server history and read tools; for example a cited fact."""
    from app.agent.runtime import run

    iterator = run(caller, request)
    try:
        async for event in iterator:
            yield event
    finally:
        await iterator.aclose()


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
