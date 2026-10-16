"""Working notes are private mutable state, available as routes and tools."""

from typing import Annotated, ClassVar
from uuid import UUID

import msgspec

from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.operations import operation
from app.infrastructure.shapes import Shape
from app.notes import service


class WriteNote(Shape):
    max_bytes: ClassVar[int] = 1024 * 1024
    title: Annotated[str, msgspec.Meta(min_length=1, max_length=200)]
    body: Annotated[str, msgspec.Meta(max_length=100000)]
    conversation_id: UUID | None = None


class NoteList(Shape):
    conversation_id: UUID | None = None


class NoteRef(Shape):
    id: UUID


class NoteSummary(Shape):
    id: UUID
    title: str
    conversation_id: UUID | None
    size_bytes: int
    created_at: str
    updated_at: str


class Note(NoteSummary):
    body: str


@operation(SIGNED_IN, put="/notes", tool=True)
def write_note(caller: Caller, request: WriteNote) -> Note:
    """Keep or replace an owned working note; for example a calculation scratchpad."""
    return Note(**service.write(caller, request))


@operation(SIGNED_IN, get="/notes", tool=True)
def list_notes(caller: Caller, request: NoteList) -> list[NoteSummary]:
    """List private working-note titles and sizes; use before reading a saved note."""
    return [NoteSummary(**row) for row in service.list_owned(caller, request.conversation_id)]


@operation(SIGNED_IN, get="/notes/{id}", tool=True)
def read_note(caller: Caller, request: NoteRef) -> Note:
    """Read one owned working note; for example a previously listed scratchpad."""
    return Note(**service.read(caller, request.id))


@operation(SIGNED_IN, delete="/notes/{id}", tool=True)
def delete_note(caller: Caller, request: NoteRef) -> None:
    """Remove one owned working note; use after its temporary work is complete."""
    service.delete(caller, request.id)
