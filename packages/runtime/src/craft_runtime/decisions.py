"""Frozen decision simulation; production adapters own authentication and storage."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, TypeVar
from uuid import uuid4

T = TypeVar("T")
STATES = {"requested", "approved", "rejected", "executing", "executed", "failed", "uncertain"}


class DecisionError(Exception):
    """Safe outcome codes contain no request, credential or callback details."""


@dataclass(frozen=True)
class Actor:
    """Trusted adapter context, never constructed from model or request-body fields."""

    user: str
    origin: str
    kind: str


@dataclass(frozen=True)
class Scope:
    owner: str
    run: str
    operation: str
    destination: str


@dataclass(frozen=True)
class Proposal:
    id: str
    scope: Scope
    arguments_json: str


@dataclass
class Entry:
    proposal: Proposal
    state: str = "requested"
    decided_by: str | None = None


def canonical(arguments: Mapping[str, Any]) -> str:
    try:
        if not isinstance(arguments, Mapping) or any(not isinstance(key, str) for key in arguments):
            raise ValueError()
        value = json.dumps(dict(arguments), sort_keys=True, ensure_ascii=False, allow_nan=False)
        if len(value.encode()) > 65536:
            raise ValueError()
        return value
    except (TypeError, ValueError):
        raise DecisionError("invalid_arguments") from None


class MemoryDecisionGate:
    """One-event-loop simulation. The callback owns its database or external-write transaction."""

    def __init__(self) -> None:
        self._entries: dict[str, Entry] = {}
        self._lock = asyncio.Lock()

    async def propose(self, scope: Scope, arguments: Mapping[str, Any]) -> Proposal:
        if any(
            not isinstance(value, str) or not 1 <= len(value) <= 200
            for value in asdict(scope).values()
        ):
            raise DecisionError("invalid_scope")
        proposal = Proposal(uuid4().hex, scope, canonical(arguments))
        async with self._lock:
            self._entries[proposal.id] = Entry(proposal)
        return proposal

    def _entry(self, identifier: str) -> Entry:
        try:
            return self._entries[identifier]
        except KeyError:
            raise DecisionError("not_found") from None

    def status(self, identifier: str) -> str:
        return self._entry(identifier).state

    async def decide(self, actor: Actor, identifier: str, *, approved: bool) -> None:
        async with self._lock:
            entry = self._entry(identifier)
            if (
                actor.user != entry.proposal.scope.owner
                or actor.origin != "http"
                or actor.kind != "human"
            ):
                raise DecisionError("decision_forbidden")
            if entry.state != "requested":
                raise DecisionError("already_decided")
            if not isinstance(approved, bool):
                raise DecisionError("invalid_decision")
            entry.state = "approved" if approved else "rejected"
            entry.decided_by = actor.user

    async def execute(
        self,
        actor: Actor,
        identifier: str,
        scope: Scope,
        arguments: Mapping[str, Any],
        apply: Callable[[dict[str, Any]], Awaitable[T]],
    ) -> T:
        supplied = canonical(arguments)
        async with self._lock:
            entry = self._entry(identifier)
            proposal = entry.proposal
            if actor.user != proposal.scope.owner:
                raise DecisionError("execution_forbidden")
            if scope != proposal.scope or supplied != proposal.arguments_json:
                raise DecisionError("request_changed")
            if entry.state != "approved":
                raise DecisionError("not_approved")
            entry.state = "executing"
        try:
            # Execution receives a fresh copy of the frozen request, never caller-owned objects.
            result = await apply(json.loads(proposal.arguments_json))
        except BaseException:
            await asyncio.shield(self._finish(identifier, "failed"))
            raise
        await asyncio.shield(self._finish(identifier, "executed"))
        return result

    async def _finish(self, identifier: str, state: str) -> None:
        async with self._lock:
            self._entry(identifier).state = state

    def snapshot(self) -> str:
        """Persist only request/decision records; never serialize callbacks or run credentials."""
        return json.dumps(
            {"version": 1, "entries": [asdict(entry) for entry in self._entries.values()]},
            sort_keys=True,
            allow_nan=False,
        )

    @classmethod
    def restore(cls, snapshot: str) -> "MemoryDecisionGate":
        gate = cls()
        try:
            saved = json.loads(snapshot)
            if saved["version"] != 1:
                raise ValueError()
            for row in saved["entries"]:
                raw = row["proposal"]
                scope = Scope(**raw["scope"])
                arguments = raw["arguments_json"]
                if canonical(json.loads(arguments)) != arguments or row["state"] not in STATES:
                    raise ValueError()
                proposal = Proposal(raw["id"], scope, arguments)
                if proposal.id in gate._entries:
                    raise ValueError()
                state = "uncertain" if row["state"] == "executing" else row["state"]
                gate._entries[proposal.id] = Entry(proposal, state, row["decided_by"])
        except (KeyError, TypeError, ValueError, DecisionError):
            raise DecisionError("invalid_snapshot") from None
        return gate
