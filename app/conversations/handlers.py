"""Owned conversation access for the runtime; public history routes use the same service."""

from typing import Annotated, Any, Literal
from uuid import UUID

import msgspec
from starlette.responses import Response

from app.conversations import service
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import RawFile, Shape


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


class NewConversation(Shape):
    title: Annotated[str, msgspec.Meta(min_length=1, max_length=200)] = "New conversation"


class ConversationUpdate(Shape):
    id: UUID
    title: Annotated[str, msgspec.Meta(min_length=1, max_length=200)] | None = None
    archived: bool | None = None


class ConversationSummary(Shape):
    id: UUID
    title: str
    updated_at: str
    archived: bool
    pending_approvals: int


class Conversation(ConversationSummary):
    messages: list[dict[str, Any]]


class FileRef(Shape):
    id: UUID
    file_id: UUID


class WorkspaceUpload(RawFile, kw_only=True):
    id: UUID
    classification: Literal["unclassified", "protected-a", "protected-b"] = "unclassified"


class WorkspaceFile(Shape, omit_defaults=True):
    id: UUID
    conversation_id: UUID
    name: str
    media_type: str
    size_bytes: int
    origin: Literal["upload", "agent"]
    state: Literal["uploading", "processing", "ready", "failed", "export-pending", "exported"]
    simulated: bool
    classification: Literal["unclassified", "protected-a", "protected-b"]
    created_at: str
    status_detail: str | None = None
    export_approval_id: UUID | None = None


@operation(SIGNED_IN, get="/conversations")
def list_conversations(caller: Caller) -> list[ConversationSummary]:
    """List up to one hundred owned conversations, newest first."""
    return [ConversationSummary(**row) for row in service.list_owned(caller)]


@operation(SIGNED_IN, post="/conversations")
def create_conversation(caller: Caller, request: NewConversation) -> ConversationSummary:
    """Create one owned conversation with a bounded title."""
    return ConversationSummary(**service.create(caller, request.title))


@operation(SIGNED_IN, get="/conversations/{id}")
def read_conversation(caller: Caller, request: ConversationRef) -> Conversation:
    """Reload owned server history, including citations and retrieval records."""
    return Conversation(**service.read(caller, request.id))


@operation(SIGNED_IN, patch="/conversations/{id}")
def update_conversation(caller: Caller, request: ConversationUpdate) -> ConversationSummary:
    """Rename or archive an owned conversation; archive deletes its workspace."""
    return ConversationSummary(**service.update(caller, request))


@operation(SIGNED_IN, get="/conversations/{id}/files")
def list_files(caller: Caller, request: ConversationRef) -> list[WorkspaceFile]:
    """List file states and labels for one owned active conversation."""
    return [WorkspaceFile(**row) for row in service.files(caller, request.id)]


@operation(SIGNED_IN, post="/conversations/{id}/files")
def upload_file(caller: Caller, request: WorkspaceUpload) -> WorkspaceFile:
    """Store one bounded raw file in an owned conversation."""
    return WorkspaceFile(**service.upload(caller, request))


@operation(SIGNED_IN, get="/conversations/{id}/files/{fileId}/content")
def file_content(caller: Caller, request: FileRef) -> Response:
    """Download exact owned file bytes as an attachment."""
    return service.content(caller, request)


@operation(SIGNED_IN, delete="/conversations/{id}/files/{fileId}")
def delete_file(caller: Caller, request: FileRef) -> None:
    """Delete one file only from its owned active conversation."""
    service.delete(caller, request)
