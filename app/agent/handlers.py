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
from app.library.handlers import CitationHandle


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
    outcome: Any = None
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

    from app.infrastructure.operations.registry import registry, tool_operations

    return [
        ToolInfo(
            name=o.tool_name,
            operation=o.name,
            description=inspect.getdoc(o.function) or "",
            effects=o.effects,
            needs_approval=o.effects != "NONE",
            route=str(o.method) + " /api" + str(o.path),
        )
        for o in tool_operations(registry)
    ]


@operation(SIGNED_IN, post="/agent/runs")
async def run_agent(caller: Caller, request: RunAgentInput) -> AsyncIterator[AGUIEvent]:
    """Stream an owned run with server history and reviewed writes; for example a cited fact."""
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


class AskAgent(Shape):
    max_bytes: ClassVar[int] = 1024 * 1024
    question: Annotated[str, msgspec.Meta(min_length=1)]
    conversation_id: UUID | None = None


class AnswerCitation(Shape):
    handle: CitationHandle
    title: str
    heading_path: str
    classification: Literal["unclassified", "protected-a", "protected-b"]
    quote: str


class AnswerConfidence(Shape):
    score: float
    level: Literal["high", "medium", "low"]
    reason: str


class AskAgentResult(Shape):
    answer: str
    citations: list[AnswerCitation]
    confidence: AnswerConfidence
    run_id: UUID


@operation(SIGNED_IN, post="/agent/ask", tool=True)
async def ask_agent(caller: Caller, request: AskAgent) -> AskAgentResult:
    """Ask the cited CRAFT runtime; for example a document question from an MCP application."""
    from dataclasses import replace
    from uuid import uuid4

    from starlette.concurrency import run_in_threadpool

    from app.conversations.handlers import NewConversation, create_conversation
    from app.infrastructure.errors import ApiError
    from app.infrastructure.operations.context import current_call

    if caller.run_id is not None:
        from app.infrastructure.errors import Forbidden

        raise Forbidden()
    conversation = request.conversation_id
    if conversation is None:
        created = await run_in_threadpool(create_conversation, caller, NewConversation())
        conversation = created.id
    identifier = uuid4()
    trusted = replace(caller, conversation_id=conversation, run_id=identifier)
    context = current_call.get()
    if context is not None:
        context.caller = trusted
    data = RunAgentInput(
        thread_id=conversation,
        run_id=identifier,
        messages=[{"id": str(uuid4()), "role": "user", "content": request.question}],
    )
    from app.agent.runtime import read_only_query

    with read_only_query():
        iterator = run_agent(trusted, data)
        text = ""
        answer = None
        finished = False
        try:
            async for event in iterator:
                if event.type == "TEXT_MESSAGE_START":
                    text = ""
                elif event.type == "TEXT_MESSAGE_CONTENT":
                    text += event.delta
                elif event.type == "ACTIVITY_SNAPSHOT" and event.activity_type == "craft.answer":
                    answer = event.content
                elif event.type == "RUN_FINISHED":
                    finished = True
        finally:
            await iterator.aclose()
        if not finished or answer is None:
            raise ApiError()
        return AskAgentResult(
            answer=text,
            citations=msgspec.convert(answer["citations"], list[AnswerCitation]),
            confidence=msgspec.convert(answer["confidence"], AnswerConfidence),
            run_id=identifier,
        )
