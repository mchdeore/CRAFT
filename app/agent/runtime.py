"""Framework events stream over server-owned history, identity and read-tool bindings."""

import asyncio
import hashlib
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, replace
from typing import Any
from uuid import uuid4, uuid5

import anyio
import msgspec
from agent_framework import (
    Agent,
    AgentSession,
)
from agent_framework_ag_ui import AGUIThreadSnapshot
from starlette.concurrency import run_in_threadpool

from app.agent import model, service
from app.agent.approval_adapter import DurableApprovalAgent
from app.agent.handlers import AGUIEvent, RunRef, issue_run_token, revoke_run_token
from app.approvals.handlers import ApprovalRequest, request_approval
from app.conversations import handlers as conversations
from app.infrastructure.errors import ApiError, Conflict, InvalidInput
from app.infrastructure.mcp.client import ToolCallMiddleware
from app.infrastructure.mcp.client import client as mcp_client
from app.infrastructure.operations.context import current_call
from app.infrastructure.operations.registry import registry, tool_named, tool_operations
from app.infrastructure.paths import ROOT
from app.skills import native as saved_skills

RUN_SECONDS = 120
TOOL_LIMIT = 20
_read_only_query = ContextVar("read_only_query", default=False)


@contextmanager
def read_only_query():
    """The single-call helper has no approval/resume channel."""
    token = _read_only_query.set(True)
    try:
        yield
    finally:
        _read_only_query.reset(token)


def fault(code):
    context = current_call.get()
    if context is not None:
        context.error_code = code


class RunLimit(ApiError):
    code, message = "run_limit", "Run limit reached."


class StatefulAgent(Agent):
    def __init__(self, *args, saved, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = saved

    def run(self, messages=None, *, stream=False, session=None, **kwargs) -> Any:
        if session is not None:
            session.state.update(self.saved.state)
            self.saved = session
        return super().run(messages, stream=stream, session=self.saved, **kwargs)


class RunSnapshots:
    """The native snapshot lives beside the serialized session in the owned run store."""

    def __init__(self, owner, thread, snapshot=None):
        self.owner, self.thread, self.snapshot = str(owner), str(thread), snapshot

    async def get(self, *, thread_id, scope=None):
        if thread_id != self.thread or scope != self.owner:
            raise Conflict()
        return AGUIThreadSnapshot(**self.snapshot) if self.snapshot is not None else None

    async def save(self, *, thread_id, snapshot, scope=None):
        if thread_id != self.thread or scope != self.owner:
            raise Conflict()
        self.snapshot = asdict(snapshot)

    async def delete(self, *, thread_id, scope=None):
        if thread_id != self.thread or scope != self.owner:
            raise Conflict()
        existed = self.snapshot is not None
        self.snapshot = None
        return existed

    async def clear(self, *, scope=None):
        if scope != self.owner:
            raise Conflict()
        self.snapshot = None


def citations(results, text):
    found = []

    def scan(value):
        if isinstance(value, dict):
            if (
                isinstance(value.get("text"), str)
                and isinstance(value.get("handle"), dict)
                and (text is None or value["text"] in text)
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


async def _run(caller, request) -> AsyncGenerator[AGUIEvent, None]:
    resuming = request.resume is not None
    newest = next(
        (message for message in reversed(request.messages) if message.get("role") == "user"), None
    )
    if not resuming and (
        newest is None
        or not isinstance(newest.get("content"), str)
        or not newest["content"].strip()
    ):
        raise InvalidInput()
    instructions = (ROOT / "app/agent/instructions.md").read_text()
    version = hashlib.sha256(instructions.encode()).hexdigest()
    ref = conversations.ConversationRef(id=request.thread_id)
    await run_in_threadpool(conversations.load_for_run, caller, ref)
    resume_payload = None
    allowed = [
        operation.tool_name
        for operation in tool_operations(registry, agent=_read_only_query.get())
        if operation.tool_name != "ask_agent"
    ]
    initial_skills = []
    if not resuming and request.skill is not None:
        initial_skills = await run_in_threadpool(
            saved_skills.load,
            caller,
            [{"id": str(request.skill.id), "revision": request.skill.revision}],
            set(allowed),
        )
    if resuming:
        state, resume_payload = await run_in_threadpool(service.resume, caller, request)
    else:
        state = await run_in_threadpool(service.start, caller, request, version)
    trusted = replace(caller, conversation_id=request.thread_id, run_id=request.run_id)
    parent = current_call.get()
    if parent is not None:
        parent.caller = trusted
    started = asyncio.get_running_loop().time()
    elapsed = 0
    deadline = started + RUN_SECONDS
    status = "failed"
    selected = None
    transport = None
    agent = None
    saved = AgentSession()
    history = []
    snapshot = None
    trace, results, budget = [], [], {"calls": 0, "exceeded": False}
    text = ""
    output = []
    existing = set()
    had_error = False
    outcome = None
    snapshots = RunSnapshots(caller.user_id, request.thread_id)
    try:
        if state:
            session_state = dict(state)
            snapshots.snapshot = session_state.pop("craftAguiSnapshot", None)
            saved = AgentSession.from_dict(session_state)
        if resuming:
            previous = saved.state.get("craft", {})
            elapsed = previous.get("activeSeconds", 0)
            budget["calls"] = previous.get("toolCalls", 0)
            trace.extend(previous.get("retrievalPath", []))
            results.extend(dict(item, text=item["quote"]) for item in previous.get("evidence", []))
            deadline = started + max(0, RUN_SECONDS - elapsed)
            if elapsed >= RUN_SECONDS:
                raise TimeoutError()
            initial_skills = await run_in_threadpool(
                saved_skills.load, trusted, saved.state.get("craftSkills", []), set(allowed)
            )
        skills = saved_skills.SavedSkillsProvider(initial_skills, allowed)
        saved.state["craftSkills"] = skills.references
        history = (await run_in_threadpool(conversations.load_for_run, trusted, ref)).messages
        user = None
        if not resuming:
            assert newest is not None
            user = {"id": str(uuid4()), "role": "user", "content": newest["content"]}
            await run_in_threadpool(
                conversations.append_for_run,
                trusted,
                conversations.AppendMessages(
                    id=request.thread_id, run=request.run_id, messages=[user]
                ),
            )
        existing = {message["id"] for message in history} | ({user["id"]} if user else set())
        mappings = {}

        def mapped(identifier):
            if identifier in existing:
                return identifier
            return mappings.setdefault(identifier, str(uuid5(request.run_id, identifier)))

        token = await run_in_threadpool(issue_run_token, trusted, RunRef(run=request.run_id))
        transport = mcp_client(
            caller.settings.mcp_url,
            token.token,
            results,
            allowed=allowed,
        )
        async with asyncio.timeout_at(deadline):
            await transport.connect()
        selected = model.client(caller.settings)
        agent = StatefulAgent(
            selected,
            instructions=instructions,
            name="craft",
            saved=saved,
            context_providers=[skills],
            tools=[transport],
            middleware=[ToolCallMiddleware(trace, budget, TOOL_LIMIT, skills.completed)],
            default_options={
                "max_tokens": caller.settings.model_max_output_tokens,
                "allow_multiple_tool_calls": False,
            },
        )
        adapter = DurableApprovalAgent(
            agent, require_confirmation=False, emit_messages_snapshot=True, snapshot_store=snapshots
        )
        if resuming and snapshots.snapshot is not None:
            adapter.restore_interrupts(
                caller.user_id, request.thread_id, snapshots.snapshot.get("interrupt") or []
            )
        data = {
            "threadId": str(request.thread_id),
            "runId": str(request.run_id),
            "messages": []
            if resuming
            else [message for message in history if message["role"] != "activity"]
            + ([user] if user else []),
            "tools": [],
            "context": [],
            "state": {},
            "__ag_ui_snapshot_scope": str(caller.user_id),
            "__ag_ui_approval_scope": str(caller.user_id),
        }
        if resume_payload is not None:
            data["resume"] = resume_payload
        async with asyncio.timeout_at(deadline):
            async for event in adapter.run(data):
                value = {
                    key: value
                    for key, value in event.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ).items()
                    if not key.startswith("_")
                }
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
                    retrieval = {
                        "id": str(uuid4()),
                        "role": "activity",
                        "activityType": "craft.retrieval",
                        "content": {
                            "messageId": value["messageId"],
                            "runId": str(request.run_id),
                            "model": caller.settings.model_main,
                            "instructionsVersion": version,
                            "skills": [dict(reference) for reference in skills.references],
                            "path": [dict(step) for step in trace],
                        },
                    }
                    output.extend(
                        [
                            {"id": value["messageId"], "role": "assistant", "content": text},
                            activity,
                            retrieval,
                        ]
                    )
                    yield AGUIEvent(
                        type="ACTIVITY_SNAPSHOT",
                        message_id=activity["id"],
                        activity_type="craft.answer",
                        content=answer,
                    )
                    continue
                if kind == "MESSAGES_SNAPSHOT":
                    snapshot = [
                        {key: field for key, field in message.items() if not key.startswith("_")}
                        for message in value["messages"]
                    ]
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
                    outcome = value.get("outcome")
                    if isinstance(outcome, dict) and outcome.get("type") == "interrupt":
                        if len(outcome.get("interrupts", [])) != 1:
                            raise RunLimit()
                        pending = []
                        for interrupt in outcome.get("interrupts", []):
                            call = (
                                interrupt.get("metadata", {})
                                .get("agent_framework", {})
                                .get("function_call", {})
                            )
                            declared = tool_named(registry, call.get("name"))
                            if (
                                declared is None
                                or declared.effects == "NONE"
                                or not isinstance(call.get("call_id"), str)
                            ):
                                raise InvalidInput()
                            arguments = dict(call["arguments"])
                            reasoning = arguments.pop("reason", "Run the requested action.")
                            approval = await run_in_threadpool(
                                request_approval,
                                trusted,
                                ApprovalRequest(
                                    run_id=request.run_id,
                                    conversation_id=request.thread_id,
                                    operation=declared.name,
                                    arguments=arguments,
                                    evidence=citations(results, None),
                                    reasoning=reasoning,
                                    retrieval_path=[dict(step) for step in trace],
                                    interrupt_id=interrupt["id"],
                                ),
                            )
                            pending.append(
                                {
                                    "id": str(approval.id),
                                    "interrupt": interrupt["id"],
                                    "callId": call["call_id"],
                                }
                            )
                            interrupt.setdefault("metadata", {})["craft"] = {
                                "approvalId": str(approval.id)
                            }
                        agent.saved.state["craftApprovals"] = pending
                        status = "paused"
                    else:
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
        try:
            with anyio.CancelScope(shield=True):
                if agent is not None:
                    saved = agent.saved
                saved.state["craft"] = {
                    "retrievalPath": trace,
                    "evidence": citations(results, None),
                    "toolCalls": budget["calls"],
                    "activeSeconds": elapsed + asyncio.get_running_loop().time() - started,
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
                    if saved.state.get("craftSkills"):
                        output.append(
                            {
                                "id": str(uuid4()),
                                "role": "activity",
                                "activityType": "craft.skills",
                                "content": {
                                    "runId": str(request.run_id),
                                    "skills": [
                                        dict(reference) for reference in saved.state["craftSkills"]
                                    ],
                                },
                            }
                        )
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
                            service.finish,
                            trusted,
                            request,
                            status,
                            saved.to_dict() | {"craftAguiSnapshot": snapshots.snapshot},
                        )
                    finally:
                        await run_in_threadpool(
                            revoke_run_token, trusted, RunRef(run=request.run_id)
                        )
        finally:
            try:
                if transport is not None:
                    await transport.close()
            finally:
                if selected is not None:
                    await selected.close()
    if status in {"finished", "paused"}:
        yield AGUIEvent(
            type="RUN_FINISHED",
            thread_id=str(request.thread_id),
            run_id=str(request.run_id),
            outcome=outcome if status == "paused" else None,
        )


async def run(caller, request) -> AsyncGenerator[AGUIEvent, None]:
    """A bounded producer keeps native MCP task scopes on their owning task."""
    queue = asyncio.Queue(maxsize=1)

    async def produce():
        iterator = _run(caller, request)
        error = None
        try:
            async for event in iterator:
                await queue.put(event)
        except asyncio.CancelledError:
            raise
        except BaseException as failure:
            error = failure
        finally:
            await iterator.aclose()
        if error is not None:
            await queue.put(error)
        await queue.put(None)

    producer = asyncio.create_task(produce())
    try:
        while True:
            event = await queue.get()
            if event is None:
                await producer
                break
            if isinstance(event, BaseException):
                raise event
            yield event
    finally:
        if not producer.done():
            producer.cancel()
        with anyio.CancelScope(shield=True):
            try:
                await producer
            except asyncio.CancelledError:
                pass
