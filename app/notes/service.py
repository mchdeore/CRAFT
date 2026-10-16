"""Owner-only note limits and optional conversation scope use short transactions."""

from typing import Any

from app.conversations.handlers import ConversationRef, read_conversation
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import Conflict, NotFound
from app.notes import storage


def scope(caller):
    if caller.user_id is None or caller.sessions is None:
        raise NotFound()
    return caller.sessions


def shape(row, body=True) -> dict[str, Any]:
    result = {
        "id": row["id"],
        "title": row["title"],
        "conversation_id": row["conversation_id"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "size_bytes": len(row["body"].encode()) if body else row["size_bytes"],
    }
    if body:
        result["body"] = row["body"]
    return result


def write(caller, request):
    if request.conversation_id is not None:
        read_conversation(caller, ConversationRef(id=request.conversation_id))
    with unit_of_work(scope(caller)) as session:
        storage.serialize(session, caller.user_id)
        existing = storage.by_title(session, caller.user_id, request.title)
        if existing is None and storage.count(session, caller.user_id) >= 500:
            raise Conflict()
        return shape(storage.write(session, caller.user_id, request, existing))


def list_owned(caller, conversation):
    if conversation is not None:
        read_conversation(caller, ConversationRef(id=conversation))
    with unit_of_work(scope(caller), read_only=True) as session:
        return [
            shape(row, False) for row in storage.list_owned(session, caller.user_id, conversation)
        ]


def read(caller, identifier):
    with unit_of_work(scope(caller), read_only=True) as session:
        row = storage.owned(session, caller.user_id, identifier)
        if row is None:
            raise NotFound()
        return shape(row)


def delete(caller, identifier):
    with unit_of_work(scope(caller)) as session:
        if storage.delete(session, caller.user_id, identifier) is None:
            raise NotFound()
