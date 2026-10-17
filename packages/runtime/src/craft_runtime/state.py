"""Server-owned run state behind a short-store interface."""

import asyncio
import copy
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_framework import Content, Message


class RuntimeErrorCode(Exception):
    pass


@dataclass(frozen=True)
class RunRequest:
    owner: str
    conversation: str
    run: str
    message: str | None = None
    resume: bool = False


@dataclass
class RunState:
    request: RunRequest
    status: str = "running"
    messages: list[dict[str, Any]] = field(default_factory=list)
    session: dict[str, Any] = field(default_factory=dict)
    pending: list[dict[str, Any]] = field(default_factory=list)
    sequence: int = 0
    tool_calls: int = 0
    used_seconds: float = 0


class RunStore(Protocol):
    async def start(self, request: RunRequest) -> RunState: ...
    async def save(self, state: RunState) -> None: ...
    async def abort(self, run: str, status: str) -> None: ...


class MemoryRunStore:
    """A simulator; no model/tool call occurs while its lock is held."""

    def __init__(self) -> None:
        self.runs: dict[str, RunState] = {}
        self.owners: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def start(self, request: RunRequest) -> RunState:
        async with self._lock:
            if not all(
                isinstance(value, str) and value
                for value in [request.owner, request.conversation, request.run]
            ):
                raise RuntimeErrorCode("invalid_identity")
            if self.owners.get(request.conversation, request.owner) != request.owner:
                raise RuntimeErrorCode("not_found")
            old = self.runs.get(request.run)
            if request.resume:
                if request.message is not None or old is None or old.status != "paused":
                    raise RuntimeErrorCode("not_paused")
                if (old.request.owner, old.request.conversation) != (
                    request.owner,
                    request.conversation,
                ):
                    raise RuntimeErrorCode("not_found")
                state = copy.deepcopy(old)
                state.request = request
                state.status = "running"
            else:
                if old is not None:
                    raise RuntimeErrorCode("run_replayed")
                if any(
                    row.request.conversation == request.conversation
                    and row.status in {"running", "paused"}
                    for row in self.runs.values()
                ):
                    raise RuntimeErrorCode("conversation_busy")
                if not isinstance(request.message, str) or not request.message.strip():
                    raise RuntimeErrorCode("invalid_message")
                history = next(
                    (
                        row.messages
                        for row in reversed(list(self.runs.values()))
                        if row.request.conversation == request.conversation
                    ),
                    [],
                )
                state = RunState(request, messages=copy.deepcopy(history))
                state.messages.append(
                    Message(role="user", contents=[Content.from_text(request.message)]).to_dict()
                )
            self.owners[request.conversation] = request.owner
            self.runs[request.run] = copy.deepcopy(state)
            return state

    async def save(self, state: RunState) -> None:
        async with self._lock:
            self.runs[state.request.run] = copy.deepcopy(state)

    async def abort(self, run: str, status: str) -> None:
        async with self._lock:
            self.runs[run].status = status
