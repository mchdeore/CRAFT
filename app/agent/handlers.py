"""Agent entry points: chat runs and their recovery, run tokens, model calls, the tool list,
and queued questions (ask_agent queues a read-only run, get_answer reads its answer)."""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any, ClassVar, Literal
from uuid import UUID

import msgspec

from app.agent import model
from app.infrastructure.access import PUBLIC, SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape
from app.jobs.handlers import JobRef
from app.library.handlers import CitationHandle
from app.skills.handlers import SkillVersionRef


class EmbeddingInput(Shape):
    text: str


class EmbeddingOutput(Shape):
    vector: Annotated[list[float], msgspec.Meta(min_length=1536, max_length=1536)]
    model: str


class EmbeddingBatchInput(Shape):
    texts: Annotated[list[str], msgspec.Meta(min_length=1)]


class EmbeddingBatchOutput(Shape):
    vectors: list[Annotated[list[float], msgspec.Meta(min_length=1536, max_length=1536)]]
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
    skill: SkillVersionRef | None = None
    # task: 60 tool calls and 15 minutes, one at a time per person, with a notification when
    # it ends or waits for approval. A resume keeps the mode the run started with.
    mode: Literal["chat", "task"] = "chat"


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
    metadata: dict[str, Any] | None = None
    subagent_run_id: str | None = None
    subtype: Literal["message", "tool-call"] | None = None
    entity_id: str | None = None
    encrypted_value: str | None = None


class ToolInfo(Shape):
    name: str
    operation: str
    description: str
    effects: str
    needs_approval: bool
    route: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    available_in: list[Literal["api", "mcp", "chat", "read_only_query"]]


class RunRef(Shape):
    run: UUID


class RunRecoveryRef(Shape):
    id: UUID


class RunRecovery(Shape):
    status: Literal["none", "running", "paused", "finished", "failed", "cancelled"]
    run_id: UUID | None
    started_at: str | None
    ended_at: str | None
    approval_id: UUID | None
    approval_status: Literal["pending", "approved", "rejected", "executed", "failed"] | None
    can_resume: bool
    # Events in the run's newest stream so far: re-attach with GET /agent/runs/{run}/events.
    last_event: int | None


@operation(SIGNED_IN, get="/conversations/{id}/run")
def get_run_recovery(caller: Caller, request: RunRecoveryRef) -> RunRecovery:
    from app.agent.service import recovery

    return recovery(caller, request)


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
    """Describe registered tool schemas and channels; for example readable library search."""
    import inspect

    from app.infrastructure.operations.registry import registry, tool_operations
    from app.infrastructure.operations.schemas import input_schema, output_schema

    read_only = {o.name for o in tool_operations(registry, agent=True)}
    result = []
    for o in tool_operations(registry):
        channels: list[Literal["api", "mcp", "chat", "read_only_query"]] = ["api", "mcp"]
        if o.tool_name != "ask_agent":
            channels.append("chat")
        if o.name in read_only:
            channels.append("read_only_query")
        result.append(
            ToolInfo(
                name=o.tool_name,
                operation=o.name,
                description=inspect.getdoc(o.function) or "",
                effects=o.effects,
                needs_approval=o.effects != "NONE",
                route=str(o.method) + " /api" + str(o.path),
                input_schema=input_schema(o),
                output_schema=output_schema(o),
                available_in=channels,
            )
        )
    return result


@operation(SIGNED_IN, post="/agent/runs")
async def run_agent(caller: Caller, request: RunAgentInput) -> AsyncIterator[AGUIEvent]:
    """Stream an owned run with server history and reviewed writes; for example a cited fact.
    The run continues on the agent worker if the connection closes."""
    from app.agent.runs import stream
    from app.agent.runtime import _read_only_query

    iterator = stream(caller, request, _read_only_query.get())
    try:
        async for event in iterator:
            yield event
    finally:
        await iterator.aclose()


class RunEventsRef(Shape):
    run: UUID
    after: Annotated[int, msgspec.Meta(ge=0, le=1_000_000)] = 0


@operation(SIGNED_IN, get="/agent/runs/{run}/events")
async def run_events(caller: Caller, request: RunEventsRef) -> AsyncIterator[AGUIEvent]:
    """Re-attach to an owned run: replay its newest stream after `after` events, then follow
    it until RUN_FINISHED or RUN_ERROR."""
    from app.agent.runs import reattach

    iterator = reattach(caller, request.run, request.after)
    try:
        async for event in iterator:
            yield event
    finally:
        await iterator.aclose()


@operation(SIGNED_IN, post="/agent/runs/{run}/cancel")
def cancel_run(caller: Caller, request: RunRef) -> None:
    """Stop an owned queued, running or paused run; its stream ends with RUN_ERROR
    run_cancelled. A paused run's pending approval is rejected."""
    from app.agent import service

    service.cancel_run(caller, request.run)


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
        model=caller.settings.model_embedding,
    )


@operation(PUBLIC)
def embed_batch(caller: Caller, request: EmbeddingBatchInput) -> EmbeddingBatchOutput:
    """Embed several already authorized source texts in order through one model client."""
    if caller.settings is None:
        from app.infrastructure.errors import NotFound

        raise NotFound()
    return EmbeddingBatchOutput(
        vectors=asyncio.run(model.embeddings(caller.settings, request.texts)),
        model=caller.settings.model_embedding,
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


class AskAgentQueued(Shape):
    job_id: str
    conversation_id: UUID


@operation(SIGNED_IN, post="/agent/ask", tool=True)
def ask_agent(caller: Caller, request: AskAgent) -> AskAgentQueued:
    """Queue a question for the cited CRAFT runtime and return its job at once; get_answer
    reads the answer. For example a document question from an MCP application."""
    from app.agent import answers

    if caller.run_id is not None:
        from app.infrastructure.errors import Forbidden

        raise Forbidden()
    job, conversation = answers.queue(caller, request.question, request.conversation_id)
    return AskAgentQueued(job_id=str(job), conversation_id=conversation)


class Answer(Shape):
    status: Literal["pending", "answered"]
    result: AskAgentResult | None


@operation(SIGNED_IN, get="/agent/answers/{jobId}", tool=True)
def get_answer(caller: Caller, request: JobRef) -> Answer:
    """Read the answer to a question ask_agent queued: pending while it runs, then the cited
    answer; for example jobId 42."""
    from app.agent import answers

    found, run = answers.read(caller, int(request.job_id))
    if found is None:
        return Answer(status="pending", result=None)
    return Answer(
        status="answered",
        result=AskAgentResult(
            answer=found["answer"],
            citations=msgspec.convert(found["citations"], list[AnswerCitation]),
            confidence=msgspec.convert(found["confidence"], AnswerConfidence),
            run_id=run,
        ),
    )
