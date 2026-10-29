"""Framework events stream over server-owned history, identity and read-tool bindings."""

import asyncio
import hashlib
import re
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

from app.accounts.handlers import reply_language
from app.agent import model, service
from app.agent.approval_adapter import DurableApprovalAgent
from app.agent.handlers import AGUIEvent, RunRef, issue_run_token, revoke_run_token
from app.agent.workspace import WorkspaceEndpoint, WorkspaceToolsets
from app.agent.workspace_run import (
    JupyterTarget,
    WorkspaceRunController,
    WorkspaceToolCallMiddleware,
)
from app.agent.workspace_session import RunBoundSession
from app.approvals.handlers import ApprovalRequest, request_approval
from app.conversations import handlers as conversations
from app.cube.handlers import ValueEvidence
from app.infrastructure import hub
from app.infrastructure.errors import ApiError, Conflict, InvalidInput
from app.infrastructure.mcp.client import ToolCallMiddleware
from app.infrastructure.mcp.client import client as mcp_client
from app.infrastructure.operations.context import current_call
from app.infrastructure.operations.registry import registry, tool_named, tool_operations
from app.infrastructure.paths import ROOT
from app.library.handlers import ChunkText, LibraryDocument, LibraryHit
from app.skills import native as saved_skills

LANGUAGES = {"en": "English", "fr": "French"}
RUN_SECONDS = 900  # ceiling for settings.agent_run_seconds
TOOL_LIMIT = 20
TASK_TOOL_LIMIT = 60  # a task-mode run's limits: 60 tool calls in RUN_SECONDS
WORKSPACE_READY_SECONDS = 90  # a stopped workspace starts with the run; wait this long for it
_read_only_query = ContextVar("read_only_query", default=False)


@contextmanager
def read_only_query():
    """The single-call helper has no approval/resume channel."""
    token = _read_only_query.set(True)
    try:
        yield
    finally:
        _read_only_query.reset(token)


def _bridge_host() -> str:
    """This pod's own address on the cluster, which the person's kernel reaches the bridge on."""
    import ipaddress
    import socket

    try:
        address = socket.gethostbyname(socket.gethostname())
        if not ipaddress.ip_address(address).is_loopback:
            return address
    except (OSError, ValueError):
        pass
    return "127.0.0.1"


async def _workspace_for_caller(caller):
    """Only the trusted caller's own Hub server; it starts here and is waited for, bounded."""
    if _read_only_query.get():
        return (
            None,
            "\nWorkspace tools are unavailable in this read-only query. "
            "Do not claim workspace access or propose workspace actions.",
            None,
        )
    found = hub.from_settings(caller.settings)
    if found is None or caller.user_id is None:
        return (
            None,
            "\nWorkspace tools are unavailable for this owner. Do not claim workspace access.",
            None,
        )
    state = await found.state(caller.user_id)
    started = state != "ready"
    if state == "stopped":
        from app.workspace.handlers import start_workspace

        await start_workspace(caller)  # the operation, so the call log has the agent's start
    if not await found.wait_ready(caller.user_id, WORKSPACE_READY_SECONDS):
        return (
            None,
            "\nThe owner's workspace did not start in time; workspace tools are unavailable for "
            "this answer. Say so and suggest trying again shortly.",
            None,
        )
    jupyter = JupyterTarget(
        found.server(caller.user_id),
        found.token,
        await found.kernel(caller.user_id),
        _bridge_host(),
    )
    endpoint = WorkspaceEndpoint(
        found.server(caller.user_id) + hub.CONTROL, found.control_headers(), hub.READ_TOOLS
    )
    selected = WorkspaceToolsets({str(caller.user_id): endpoint}).for_caller(caller)
    guidance = (
        "\nUSER_ROOT=/workspace. Only the supplied workspace_ list/notebook read tools "
        "are available; use their advertised schemas. "
    )
    guidance += (
        "A Jupyter kernel is configured. run_code, write_file, edit_notebook and save_chart "
        "require "
        "the requester's approval. Read plain files and selected Markdown skills with "
        "small approved run_code calls."
    )
    if started:
        guidance += " The owner's workspace was started for this answer; say so when you use it."
    from app.approvals.handlers import GrantQuery, list_notebook_grants

    try:
        granted = (
            await run_in_threadpool(
                list_notebook_grants, caller, GrantQuery(conversation_id=caller.conversation_id)
            )
            if caller.conversation_id is not None
            else []
        )
    except ApiError:
        # Without readable grants every notebook edit asks, as before.
        granted = []
    if granted:
        guidance += (
            " Notebooks granted in this conversation (use edit_granted_notebook and "
            "run_in_granted_notebook for these, without asking): "
            + ", ".join(sorted({grant.path for grant in granted}))
            + "."
        )
    return selected, guidance, jupyter


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


def source_link(handle) -> str:
    return (
        f"/sources/{handle.get('item')}?version={handle.get('version')}&block={handle.get('block')}"
    )


def linked_in(handle, text: str) -> bool:
    """The text links this exact block; a link to block 12 does not also link block 1."""
    return re.search(re.escape(source_link(handle)) + r"(?!\d)", text) is not None


def citations(results, text, linked=False):
    """Verified quotes the answer uses; with `linked`, a source link also counts (paraphrases)."""
    found = []
    chunks = []
    neighbors = []

    def scan(value):
        if isinstance(value, dict):
            if value.get("kind") == "chunk":
                try:
                    hit = msgspec.convert(value, LibraryHit)
                except msgspec.ValidationError:
                    hit = None
                if hit is not None and hit.title.strip():
                    for neighbor in (hit.before, hit.after):
                        if (
                            neighbor is not None
                            and neighbor.text
                            and (
                                neighbor.handle.item,
                                neighbor.handle.version,
                                neighbor.handle.simulated,
                            )
                            == (hit.handle.item, hit.handle.version, hit.handle.simulated)
                        ):
                            neighbors.append(
                                {
                                    "text": neighbor.text,
                                    "handle": msgspec.to_builtins(neighbor.handle),
                                    "title": hit.title,
                                    "headingPath": neighbor.heading_path,
                                    "classification": hit.classification,
                                }
                            )
            if (
                isinstance(value.get("text"), str)
                and isinstance(value.get("handle"), dict)
                and (
                    text is None
                    or value["text"] in text
                    or (linked and linked_in(value["handle"], text))
                )
                and hashlib.sha256(value["text"].encode()).hexdigest()
                == value["handle"].get("quoteSha256")
            ):
                handle = value["handle"]
                if "title" not in value:
                    chunks.append(value)
                elif all(handle != row["handle"] for row in found):
                    found.append(
                        {
                            "handle": handle,
                            "title": value["title"],
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

    for neighbor in neighbors:
        if not any(
            other["handle"] == neighbor["handle"] and other != neighbor for other in neighbors
        ):
            scan(neighbor)

    def retrieved(shape):
        for result in results:
            if isinstance(result, dict):
                try:
                    yield msgspec.convert(result, shape)
                except msgspec.ValidationError:
                    continue

    documents = list(retrieved(LibraryDocument))

    def document_metadata(handle):
        matches = [
            document
            for document in documents
            if (document.item, document.version) == (handle.item, handle.version)
        ]
        if not matches or any(document != matches[0] for document in matches):
            return None
        document = matches[0]
        headings = {row.heading_path for row in document.outline if row.block == handle.block}
        if (
            document.deleted
            or not document.title.strip()
            or document.simulated != handle.simulated
            or len(headings) != 1
        ):
            return None
        return {
            "title": document.title,
            "headingPath": headings.pop(),
            "classification": document.classification,
        }

    for value in chunks:
        try:
            chunk = msgspec.convert(value, ChunkText)
        except msgspec.ValidationError:
            continue
        metadata = document_metadata(chunk.handle)
        if metadata is not None and chunk.text and chunk.heading_path == metadata["headingPath"]:
            scan({"text": chunk.text, "handle": msgspec.to_builtins(chunk.handle), **metadata})

    for evidence in retrieved(ValueEvidence):
        value = evidence.value
        handle = value.citation
        metadata = document_metadata(handle)
        if (
            metadata is None
            or not evidence.quote
            or value.withdrawn
            or evidence.citation_status == "withdrawn"
            or value.simulated != handle.simulated
        ):
            continue
        scan(
            {
                "text": evidence.quote,
                "handle": msgspec.to_builtins(handle),
                **metadata,
            }
        )
    return found


def language_instructions(language: str) -> str:
    return f"\n\nReply language for this run: {LANGUAGES[language]}.\n"


def originals(message_id: str, cited, language: str) -> dict | None:
    """The English quotes behind a reply in another language; every source is English for now."""
    if language == "en":
        return None
    items = []
    for citation in cited:
        try:
            items.append(
                {"handle": citation["handle"], "language": "en", "text": citation["quote"]}
            )
        except (KeyError, TypeError):
            continue
    return {"messageId": message_id, "items": items} if items else None


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
    language = await run_in_threadpool(reply_language, caller)
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
        state, resume_payload, mode = await run_in_threadpool(service.resume, caller, request)
    else:
        mode = request.mode
        state = await run_in_threadpool(service.start, caller, request, version)
    trusted = replace(caller, conversation_id=request.thread_id, run_id=request.run_id)
    parent = current_call.get()
    if parent is not None:
        parent.caller = trusted
    started = asyncio.get_running_loop().time()
    elapsed = 0
    task = mode == "task"
    limit = RUN_SECONDS if task else min(RUN_SECONDS, caller.settings.agent_run_seconds)
    tool_limit = TASK_TOOL_LIMIT if task else TOOL_LIMIT
    deadline = started + limit
    status = "failed"
    end_reason = "failed"
    selected = None
    transport = None
    workspace_transport = None
    workspace_controller = None
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
            # A paused run resumes in the language it started in, whatever the setting is now.
            language = previous.get("replyLanguage", language)
            elapsed = previous.get("activeSeconds", 0)
            budget["calls"] = previous.get("toolCalls", 0)
            trace.extend(previous.get("retrievalPath", []))
            results.extend(dict(item, text=item["quote"]) for item in previous.get("evidence", []))
            deadline = started + max(0, limit - elapsed)
            if elapsed >= limit:
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
            workspace_transport, workspace_instructions, jupyter = await _workspace_for_caller(
                trusted
            )
            if workspace_transport is not None:
                await workspace_transport.connect()
        external_tools = (
            frozenset(function.name for function in workspace_transport.functions)
            if workspace_transport is not None
            else frozenset()
        )
        if any(tool_named(registry, name) is not None for name in external_tools):
            raise ApiError()
        middleware = (
            WorkspaceToolCallMiddleware(
                trace,
                budget,
                tool_limit,
                skills.completed,
                external_tools=external_tools,
                kernel_id=jupyter.kernel_id,
            )
            if jupyter is not None
            else ToolCallMiddleware(
                trace, budget, tool_limit, skills.completed, external_tools=external_tools
            )
        )
        if jupyter is not None:
            if parent is None:
                raise ApiError()
            workspace_controller = WorkspaceRunController(
                RunBoundSession(transport, middleware, context=parent, deadline=deadline), jupyter
            )
            workspace_instructions += await workspace_controller.start()
        selected = model.client(caller.settings)
        agent = StatefulAgent(
            selected,
            instructions=instructions + language_instructions(language) + workspace_instructions,
            name="craft",
            saved=saved,
            context_providers=[skills],
            tools=[transport] + ([workspace_transport] if workspace_transport is not None else []),
            middleware=[middleware],
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
                if kind == "REASONING_ENCRYPTED_VALUE" and value["subtype"] == "message":
                    value["entityId"] = mapped(value["entityId"])
                if kind == "TEXT_MESSAGE_START":
                    text = ""
                if kind == "TEXT_MESSAGE_CONTENT":
                    text += value["delta"]
                if kind == "TEXT_MESSAGE_END":
                    if not text:
                        yield msgspec.convert(value, AGUIEvent)
                        continue
                    cited = citations(results, text, linked=language != "en")
                    answer = {
                        "messageId": value["messageId"],
                        "citations": cited,
                        "confidence": {
                            "score": 0.0,
                            "level": "low",
                            "reason": "Evidence is insufficient for drafting before calibration.",
                        },
                    }
                    yield msgspec.convert(value, AGUIEvent)
                    found = originals(value["messageId"], cited, language)
                    activity = {
                        "id": str(uuid4()),
                        "role": "activity",
                        "activityType": "craft.answer",
                        "content": answer,
                    }
                    shown = (
                        {
                            "id": str(uuid4()),
                            "role": "activity",
                            "activityType": "craft.originals",
                            "content": found,
                        }
                        if found
                        else None
                    )
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
                            "replyLanguage": language,
                            "originals": len(found["items"]) if found else 0,
                        },
                    }
                    # Saved before the next yield: a cancel while a snapshot waits keeps them all.
                    output.extend(
                        [
                            {"id": value["messageId"], "role": "assistant", "content": text},
                            activity,
                            *([shown] if shown else []),
                            retrieval,
                        ]
                    )
                    yield AGUIEvent(
                        type="ACTIVITY_SNAPSHOT",
                        message_id=activity["id"],
                        activity_type="craft.answer",
                        content=answer,
                    )
                    if shown:
                        yield AGUIEvent(
                            type="ACTIVITY_SNAPSHOT",
                            message_id=shown["id"],
                            activity_type="craft.originals",
                            content=found,
                        )
                    continue
                if kind == "MESSAGES_SNAPSHOT":
                    snapshot = [
                        {key: field for key, field in message.items() if not key.startswith("_")}
                        for message in value["messages"]
                    ]
                    # The framework re-identifies replayed history, so only messages after the
                    # newest user turn belong to this run; earlier ones come from stored history.
                    latest = max(
                        (i for i, message in enumerate(snapshot) if message.get("role") == "user"),
                        default=-1,
                    )
                    snapshot = snapshot[latest + 1 :]
                    for message in snapshot:
                        if message.get("id"):
                            message["id"] = mapped(message["id"])
                    value["messages"] = list(
                        {
                            message["id"]: message
                            for message in history + ([user] if user else []) + snapshot + output
                        }.values()
                    )
                if kind == "RUN_ERROR":
                    status = "failed"
                    end_reason = "failed"
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
                        end_reason = "paused"
                    else:
                        status = "failed" if had_error else "finished"
                        end_reason = status
                    continue
                yield msgspec.convert(value, AGUIEvent)
    except (asyncio.CancelledError, GeneratorExit):
        status = "cancelled"
        end_reason = "cancelled"
        raise
    except TimeoutError:
        status = "failed"
        end_reason = "time_limit"
        fault("run_timeout")
        yield AGUIEvent(type="RUN_ERROR", code="run_timeout", message="Run time limit reached.")
    except ApiError as error:
        status = "failed"
        end_reason = "tool_limit" if error.code == "run_limit" else "failed"
        fault(error.code)
        yield AGUIEvent(type="RUN_ERROR", code=error.code, message=type(error).message)
    except Exception:
        status = "failed"
        end_reason = "failed"
        fault("internal_error")
        yield AGUIEvent(type="RUN_ERROR", code="internal_error", message="Run failed.")
    finally:
        try:
            with anyio.CancelScope(shield=True):
                if workspace_controller is not None:
                    try:
                        await workspace_controller.close()
                    except Exception:
                        status = "failed"
                        fault("internal_error")
                if agent is not None:
                    saved = agent.saved
                saved.state["craft"] = {
                    "retrievalPath": trace,
                    "evidence": citations(results, None),
                    "toolCalls": budget["calls"],
                    "activeSeconds": elapsed + asyncio.get_running_loop().time() - started,
                    "model": caller.settings.model_main,
                    "instructionsVersion": version,
                    "replyLanguage": language,
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
                        active = elapsed + asyncio.get_running_loop().time() - started
                        state = saved.to_dict() | {"craftAguiSnapshot": snapshots.snapshot}
                        calls, reason = budget["calls"], end_reason

                        def persist():
                            service.finish(
                                trusted,
                                request,
                                status,
                                state,
                                tool_calls=calls,
                                active_seconds=active,
                                end_reason=reason,
                            )

                        await run_in_threadpool(persist)
                    finally:
                        await run_in_threadpool(
                            revoke_run_token, trusted, RunRef(run=request.run_id)
                        )
        finally:
            try:
                if workspace_transport is not None:
                    with anyio.CancelScope(shield=True):
                        try:
                            async with asyncio.timeout_at(deadline):
                                await workspace_transport.close()
                        except TimeoutError:
                            # LocalHTTPTool.close also releases HTTP when session close times out.
                            pass
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
