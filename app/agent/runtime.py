"""Framework events stream over server-owned history, identity and read-tool bindings."""

import asyncio
import hashlib
import inspect
from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4, uuid5

import anyio
import msgspec
from agent_framework import (
    Agent,
    AgentSession,
    FunctionInvocationContext,
    FunctionMiddleware,
    FunctionTool,
)
from agent_framework_ag_ui import AgentFrameworkAgent
from starlette.concurrency import run_in_threadpool

from app.agent import model, service
from app.agent.handlers import AGUIEvent
from app.conversations import handlers as conversations
from app.infrastructure.errors import ApiError, Conflict, InvalidInput
from app.infrastructure.mcp.server import input_schema
from app.infrastructure.operations.context import current_call
from app.infrastructure.operations.registry import registry
from app.infrastructure.paths import ROOT

RUN_SECONDS = 120
TOOL_LIMIT = 20


def fault(code):
    context = current_call.get()
    if context is not None:
        context.error_code = code


class RunLimit(ApiError):
    code, message = "run_limit", "Run limit reached."


class ToolCallMiddleware(FunctionMiddleware):
    async def process(self, context, call_next):
        await call_next()


class StatefulAgent(Agent):
    def __init__(self, *args, saved, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = saved

    def run(self, messages=None, *, stream=False, session=None, **kwargs) -> Any:
        if session is not None:
            session.state.update(self.saved.state)
            self.saved = session
        return super().run(messages, stream=stream, session=self.saved, **kwargs)


def tools(caller, trace, budget, results):
    bindings = []

    def bind(op):
        async def invoke(ctx: FunctionInvocationContext, **arguments):
            budget["calls"] += 1
            if budget["calls"] > TOOL_LIMIT:
                budget["exceeded"] = True
                raise RunLimit()
            identifier = ctx.metadata.get("call_id")
            if not isinstance(identifier, str) or not identifier:
                raise ApiError()
            trusted = replace(caller, origin="python", tool_call_id=identifier)
            entry = {
                "tool": op.tool_name,
                "operation": op.name,
                "arguments": arguments,
                "at": datetime.now(UTC).isoformat(),
                "toolCallId": identifier,
            }
            trace.append(entry)
            value = (
                await op.function(trusted, arguments)
                if inspect.iscoroutinefunction(op.function)
                else await run_in_threadpool(op.function, trusted, arguments)
            )
            encoded = msgspec.to_builtins(value)
            results.append(encoded)
            return encoded

        return FunctionTool(
            name=op.tool_name,
            description=inspect.getdoc(op.function) or "",
            input_model=input_schema(op),
            func=invoke,
            approval_mode="never_require",
        )

    for op in sorted(registry.values(), key=lambda o: o.tool_name):
        if op.tool and op.effects == "NONE":
            bindings.append(bind(op))
    return bindings


def citations(results, text):
    found = []

    def scan(value):
        if isinstance(value, dict):
            if (
                isinstance(value.get("text"), str)
                and isinstance(value.get("handle"), dict)
                and value["text"] in text
                and hashlib.sha256(value["text"].encode()).hexdigest()
                == value["handle"].get("quoteSha256")
            ):
                handle = value["handle"]
                if all(handle != row["handle"] for row in found):
                    found.append(
                        {
                            "handle": handle,
                            "title": value.get("title", "Simulated evidence"),
                            "headingPath": value.get("headingPath", ""),
                            "classification": value.get("classification", "unclassified"),
                            "quote": value["text"],
                        }
                    )
            for child in value.values():
                scan(child)
        elif isinstance(value, list):
            for child in value:
                scan(child)

    for result in results:
        scan(result)
    return found


async def run(caller, request) -> AsyncGenerator[AGUIEvent, None]:
    if request.resume is not None:
        raise Conflict()
    newest = next(
        (message for message in reversed(request.messages) if message.get("role") == "user"), None
    )
    if (
        newest is None
        or not isinstance(newest.get("content"), str)
        or not newest["content"].strip()
    ):
        raise InvalidInput()
    instructions = (ROOT / "app/agent/instructions.md").read_text()
    version = hashlib.sha256(instructions.encode()).hexdigest()
    ref = conversations.ConversationRef(id=request.thread_id)
    await run_in_threadpool(conversations.load_for_run, caller, ref)
    state = await run_in_threadpool(service.start, caller, request, version)
    trusted = replace(caller, conversation_id=request.thread_id, run_id=request.run_id)
    parent = current_call.get()
    if parent is not None:
        parent.caller = trusted
    status = "failed"
    selected = None
    agent = None
    saved = AgentSession()
    history = []
    snapshot = None
    trace, results, budget = [], [], {"calls": 0, "exceeded": False}
    text = ""
    output = []
    existing = set()
    had_error = False
    try:
        if state:
            saved = AgentSession.from_dict(state)
        history = (await run_in_threadpool(conversations.load_for_run, trusted, ref)).messages
        user = {"id": str(uuid4()), "role": "user", "content": newest["content"]}
        await run_in_threadpool(
            conversations.append_for_run,
            trusted,
            conversations.AppendMessages(id=request.thread_id, run=request.run_id, messages=[user]),
        )
        existing = {message["id"] for message in history} | {user["id"]}
        mappings = {}

        def mapped(identifier):
            if identifier in existing:
                return identifier
            return mappings.setdefault(identifier, str(uuid5(request.run_id, identifier)))

        selected = model.client(caller.settings)
        agent = StatefulAgent(
            selected,
            instructions=instructions,
            name="craft",
            saved=saved,
            context_providers=[],
            tools=tools(trusted, trace, budget, results),
            middleware=[ToolCallMiddleware()],
            default_options={"max_tokens": caller.settings.model_max_output_tokens},
        )
        adapter = AgentFrameworkAgent(
            agent, require_confirmation=False, emit_messages_snapshot=True
        )
        data = {
            "threadId": str(request.thread_id),
            "runId": str(request.run_id),
            "messages": [message for message in history if message["role"] != "activity"] + [user],
            "tools": [],
            "context": [],
            "state": {},
        }
        async with asyncio.timeout(RUN_SECONDS):
            async for event in adapter.run(data):
                value = event.model_dump(mode="json", by_alias=True, exclude_none=True)
                if budget["exceeded"]:
                    raise RunLimit()
                kind = value["type"]
                for key in ["messageId", "parentMessageId"]:
                    if value.get(key):
                        value[key] = mapped(value[key])
                if kind == "TEXT_MESSAGE_START":
                    text = ""
                if kind == "TEXT_MESSAGE_CONTENT":
                    text += value["delta"]
                if kind == "TEXT_MESSAGE_END":
                    if not text:
                        yield msgspec.convert(value, AGUIEvent)
                        continue
                    answer = {
                        "messageId": value["messageId"],
                        "citations": citations(results, text),
                        "confidence": {
                            "score": 0.0,
                            "level": "low",
                            "reason": "Evidence is insufficient for drafting before calibration.",
                        },
                    }
                    yield msgspec.convert(value, AGUIEvent)
                    activity = {
                        "id": str(uuid4()),
                        "role": "activity",
                        "activityType": "craft.answer",
                        "content": answer,
                    }
                    output.extend(
                        [{"id": value["messageId"], "role": "assistant", "content": text}, activity]
                    )
                    yield AGUIEvent(
                        type="ACTIVITY_SNAPSHOT",
                        message_id=activity["id"],
                        activity_type="craft.answer",
                        content=answer,
                    )
                    continue
                if kind == "MESSAGES_SNAPSHOT":
                    snapshot = value["messages"]
                    for message in snapshot:
                        if message.get("id"):
                            message["id"] = mapped(message["id"])
                    value["messages"] = list(
                        {message["id"]: message for message in snapshot + output}.values()
                    )
                if kind == "RUN_ERROR":
                    status = "failed"
                    had_error = True
                    fault(value.get("code", "internal_error"))
                if kind == "RUN_FINISHED":
                    status = "failed" if had_error else "finished"
                    continue
                yield msgspec.convert(value, AGUIEvent)
    except (asyncio.CancelledError, GeneratorExit):
        status = "cancelled"
        raise
    except TimeoutError:
        status = "failed"
        fault("run_timeout")
        yield AGUIEvent(type="RUN_ERROR", code="run_timeout", message="Run time limit reached.")
    except ApiError as error:
        status = "failed"
        fault(error.code)
        yield AGUIEvent(type="RUN_ERROR", code=error.code, message=type(error).message)
    except Exception:
        status = "failed"
        fault("internal_error")
        yield AGUIEvent(type="RUN_ERROR", code="internal_error", message="Run failed.")
    finally:
        with anyio.CancelScope(shield=True):
            if agent is not None:
                saved = agent.saved
            saved.state["craft"] = {
                "retrievalPath": trace,
                "model": caller.settings.model_main,
                "instructionsVersion": version,
            }
            if snapshot is not None:
                output = list(
                    {
                        message["id"]: message
                        for message in [
                            message for message in snapshot if message.get("id") not in existing
                        ]
                        + output
                    }.values()
                )
            try:
                if output:
                    await run_in_threadpool(
                        conversations.append_for_run,
                        trusted,
                        conversations.AppendMessages(
                            id=request.thread_id, run=request.run_id, messages=output
                        ),
                    )
            except Exception:
                status = "failed"
                raise ApiError() from None
            finally:
                try:
                    await run_in_threadpool(
                        service.finish, trusted, request, status, saved.to_dict()
                    )
                finally:
                    if selected is not None:
                        await selected.close()
    if status == "finished":
        yield AGUIEvent(
            type="RUN_FINISHED", thread_id=str(request.thread_id), run_id=str(request.run_id)
        )
