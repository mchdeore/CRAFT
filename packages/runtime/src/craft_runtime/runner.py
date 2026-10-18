"""A bounded native-framework runner with injected stores, tools and credentials."""

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import anyio
from agent_framework import Agent, AgentSession, Content, FunctionMiddleware, Message

from craft_runtime.decisions import Actor, MemoryDecisionGate, Scope
from craft_runtime.state import RunRequest, RunState, RunStore, RuntimeErrorCode


@dataclass(frozen=True)
class Binding:
    destination: str
    effectful: bool = False


class ToolSource(Protocol):
    @property
    def functions(self) -> Sequence[Any]: ...

    @property
    def bindings(self) -> Mapping[str, Binding]: ...

    async def connect(self) -> None: ...
    async def close(self) -> None: ...


class Credentials(Protocol):
    async def issue(self, request: RunRequest) -> str: ...
    async def revoke(self, request: RunRequest) -> None: ...


@dataclass(frozen=True)
class Event:
    sequence: int
    kind: str
    data: dict[str, Any]


def references(results: list[Any], text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def scan(value):
        if isinstance(value, dict):
            quote, handle = value.get("text"), value.get("handle")
            if isinstance(quote, str) and quote and quote in text and isinstance(handle, dict):
                if hashlib.sha256(quote.encode()).hexdigest() == handle.get("quoteSha256"):
                    if handle not in [row["handle"] for row in found]:
                        found.append({"text": quote, "handle": handle})
            for child in value.values():
                scan(child)
        elif isinstance(value, list):
            for child in value:
                scan(child)

    for result in results:
        scan(result)
    return found


class BoundCalls(FunctionMiddleware):
    def __init__(
        self, state: RunState, source: ToolSource, decisions: MemoryDecisionGate, limit: int
    ):
        self.state, self.source, self.decisions, self.limit = state, source, decisions, limit
        self.calls = state.tool_calls
        self.error: str | None = None
        self.results: list[Any] = []

    async def process(self, context, call_next):
        self.calls += 1
        self.state.tool_calls = self.calls
        if self.calls > self.limit:
            self.error = "tool_limit"
            raise RuntimeErrorCode(self.error)
        name = context.function.name
        if name == "ask_agent" or name not in self.source.bindings:
            self.error = "tool_refused"
            raise RuntimeErrorCode(self.error)
        binding = self.source.bindings[name]
        try:
            if binding.effectful:
                identifier = context.metadata.get("call_id")
                pending = next(
                    (row for row in self.state.pending if row["callId"] == identifier), None
                )
                if pending is None:
                    raise RuntimeErrorCode("decision_missing")
                scope = Scope(
                    self.state.request.owner, self.state.request.run, name, binding.destination
                )

                async def approved_call(arguments):
                    context.arguments = arguments
                    await call_next()
                    return context.result

                arguments = context.arguments
                if not isinstance(arguments, Mapping):
                    arguments = arguments.model_dump(mode="json")
                await self.decisions.execute(
                    Actor(self.state.request.owner, "mcp", "agent"),
                    pending["proposal"],
                    scope,
                    arguments,
                    approved_call,
                )
            else:
                await call_next()
            raw = context.result
            if isinstance(raw, list):
                raw = "".join(content.text or "" for content in raw if content.type == "text")
            if not isinstance(raw, str):
                raise ValueError()
            value = json.loads(raw)
            if not isinstance(value, (dict, list)):
                raise ValueError()
            self.results.append(value)
        except Exception:
            self.error = self.error or "tool_failed"
            raise RuntimeErrorCode(self.error) from None


class Runtime:
    def __init__(
        self,
        *,
        store: RunStore,
        decisions: MemoryDecisionGate,
        credentials: Credentials,
        model_factory: Callable[[], Any],
        tools_factory: Callable[[RunRequest, str], ToolSource],
        seconds: float = 120,
        tool_limit: int = 20,
        max_tokens: int = 4000,
    ):
        self.store, self.decisions, self.credentials = store, decisions, credentials
        self.model_factory, self.tools_factory = model_factory, tools_factory
        self.seconds, self.tool_limit, self.max_tokens = seconds, tool_limit, max_tokens

    async def _execute(self, request: RunRequest) -> AsyncGenerator[Event, None]:
        state = await self.store.start(request)
        source = model = None
        issued = False
        session = AgentSession.from_dict(state.session) if state.session else AgentSession()
        terminal, data = "error", {"code": "run_failed"}
        active_started = asyncio.get_running_loop().time()

        def event(kind, data):
            state.sequence += 1
            return Event(state.sequence, kind, data)

        try:
            yield event("started", {"run": request.run})
            raw = await self.credentials.issue(request)
            issued = True
            source = self.tools_factory(request, raw)
            async with asyncio.timeout(self.seconds - state.used_seconds):
                await source.connect()
                allowed = [
                    function
                    for function in source.functions
                    if function.name != "ask_agent" and function.name in source.bindings
                ]
                for function in allowed:
                    if (
                        source.bindings[function.name].effectful
                        and function.approval_mode != "always_require"
                    ):
                        raise RuntimeErrorCode("invalid_tool_configuration")
                model = self.model_factory()
                calls = BoundCalls(state, source, self.decisions, self.tool_limit)
                agent = Agent(
                    model,
                    context_providers=[],
                    tools=allowed,
                    middleware=[calls],
                    default_options={"max_tokens": self.max_tokens},
                )
                messages = [Message.from_dict(message) for message in state.messages]
                if request.resume:
                    for row in state.pending:
                        status = self.decisions.status(row["proposal"])
                        if status not in {"approved", "rejected"}:
                            raise RuntimeErrorCode("decision_pending")
                        native = Content.from_dict(row["native"])
                        if not isinstance(native.id, str) or native.function_call is None:
                            raise RuntimeErrorCode("invalid_checkpoint")
                        messages.append(
                            Message(
                                role="user",
                                contents=[
                                    Content.from_function_approval_response(
                                        status == "approved", native.id, native.function_call
                                    )
                                ],
                            )
                        )
                async with agent.run(messages, session=session, stream=True) as stream:
                    text, pending = "", []
                    async for update in stream:
                        if calls.error:
                            raise RuntimeErrorCode(calls.error)
                        for content in update.contents:
                            if content.type == "function_call" and content.name not in [
                                function.name for function in allowed
                            ]:
                                raise RuntimeErrorCode("tool_refused")
                            if content.type == "text" and update.role == "assistant":
                                text += content.text or ""
                                if (
                                    len(re.findall(r"\w+|[^\w\s]", text)) > self.max_tokens
                                    or len(text.encode()) > self.max_tokens * 16
                                ):
                                    raise RuntimeErrorCode("output_limit")
                            if content.type == "function_approval_request":
                                call = content.function_call
                                if (
                                    call is None
                                    or not isinstance(call.name, str)
                                    or not isinstance(call.call_id, str)
                                    or not isinstance(content.id, str)
                                ):
                                    raise RuntimeErrorCode("invalid_tool_request")
                                name = call.name
                                binding = source.bindings.get(name)
                                if binding is None or not binding.effectful:
                                    raise RuntimeErrorCode("tool_refused")
                                arguments = (
                                    json.loads(call.arguments)
                                    if isinstance(call.arguments, str)
                                    else call.arguments
                                )
                                if not isinstance(arguments, dict):
                                    raise RuntimeErrorCode("invalid_tool_request")
                                proposal = await self.decisions.propose(
                                    Scope(request.owner, request.run, name, binding.destination),
                                    arguments,
                                )
                                pending.append(
                                    {
                                        "proposal": proposal.id,
                                        "callId": call.call_id,
                                        "native": content.to_dict(),
                                    }
                                )
                        yield event("update", update.to_dict())
                    final = await stream.get_final_response()
                    if calls.error:
                        raise RuntimeErrorCode(calls.error)
            state.messages.extend(message.to_dict() for message in final.messages)
            state.session = session.to_dict()
            state.used_seconds += asyncio.get_running_loop().time() - active_started
            state.pending = pending
            state.status = "paused" if pending else "finished"
            terminal = state.status
            data = {
                "run": request.run,
                "decisions": [row["proposal"] for row in pending],
                "citations": references(calls.results, text),
            }
            await self.store.save(state)
        except (asyncio.CancelledError, GeneratorExit):
            with anyio.CancelScope(shield=True):
                await self.store.abort(request.run, "cancelled")
            raise
        except Exception as error:
            terminal = "error"
            code = (
                "run_timeout"
                if isinstance(error, TimeoutError)
                else str(error)
                if isinstance(error, RuntimeErrorCode)
                else "run_failed"
            )
            data = {"code": code}
            await self.store.abort(
                request.run, "paused" if code == "decision_pending" else "failed"
            )
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    try:
                        if issued:
                            async with asyncio.timeout(5):
                                await self.credentials.revoke(request)
                    finally:
                        try:
                            if source is not None:
                                async with asyncio.timeout(5):
                                    await source.close()
                        finally:
                            if model is not None:
                                async with asyncio.timeout(5):
                                    await model.close()
                except Exception:
                    terminal, data = "error", {"code": "cleanup_failed"}
                    try:
                        await self.store.abort(request.run, "failed")
                    except Exception:
                        raise RuntimeErrorCode("state_unavailable") from None
        yield event(terminal, data)

    async def run(self, request: RunRequest) -> AsyncGenerator[Event, None]:
        """The producer owns native tool task scopes from connection through close."""
        queue: asyncio.Queue[Event | BaseException | None] = asyncio.Queue(maxsize=1)

        async def produce():
            iterator = self._execute(request)
            try:
                async for event in iterator:
                    await queue.put(event)
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                await queue.put(error)
            finally:
                await iterator.aclose()
            await queue.put(None)

        producer = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    await producer
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            if not producer.done():
                producer.cancel()
            with anyio.CancelScope(shield=True):
                try:
                    await producer
                except asyncio.CancelledError:
                    pass
