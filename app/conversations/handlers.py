"""Owned conversation access for the runtime; public history routes use the same service."""

from typing import Any
from uuid import UUID

from app.conversations import service
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape


class ConversationRef(Shape):
    id: UUID


class RuntimeConversation(Shape):
    id: UUID
    messages: list[dict[str, Any]]


class AppendMessages(Shape):
    id: UUID
    run: UUID
    messages: list[dict[str, Any]]


@operation(SIGNED_IN)
def load_for_run(caller: Caller, request: ConversationRef) -> RuntimeConversation:
    """Load one owned, active conversation and its server history for a run."""
    return RuntimeConversation(id=request.id, messages=service.load(caller, request.id))


@operation(SIGNED_IN)
def append_for_run(caller: Caller, request: AppendMessages) -> None:
    """Append server-built messages to an owned conversation through a checked private entry."""
    service.append(caller, request)
