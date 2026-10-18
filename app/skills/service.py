"""Atomic owner/head updates, immutable revision replay and current tool references."""

import hashlib
import json
from typing import Any

import msgspec

from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import Conflict, InvalidInput, NotFound
from app.infrastructure.operations.registry import registry, tool_operations
from app.skills import storage


def scope(caller):
    if caller.user_id is None or caller.sessions is None:
        raise NotFound()
    return caller.sessions


def tools():
    return {operation.tool_name for operation in tool_operations(registry)}


def shape(row, known, *, summary=False) -> dict[str, Any]:
    result = {key: row[key] for key in ("id", "revision", "name", "description", "tool_names")}
    result.update(
        unavailable_tools=[name for name in row["tool_names"] if name not in known],
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )
    if not summary:
        result.update(instructions=row["instructions"], example_inputs=row["example_inputs"])
    return result


def digest(request):
    canonical = json.dumps(
        msgspec.to_builtins(request), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def save(caller, request):
    request_hash = digest(request)
    with unit_of_work(scope(caller)) as session:
        storage.serialize(session, caller.user_id)
        previous = storage.replay(session, caller.user_id, request.mutation_id)
        if previous is not None:
            if previous["request_hash"] != request_hash:
                raise Conflict()
            return shape(previous, tools())
        current = storage.head(session, caller.user_id, request.id)
        if current is None and request.expected_revision != 0:
            raise NotFound()
        known = tools()
        if len(set(request.tool_names)) != len(request.tool_names) or any(
            name not in known for name in request.tool_names
        ):
            raise InvalidInput("Choose distinct registered tool references.")
        if current is None:
            storage.create(session, caller.user_id, request.id)
            current = storage.head(session, caller.user_id, request.id)
            if current is None:
                raise NotFound()
        if current["revision"] != request.expected_revision:
            raise Conflict()
        storage.append(session, caller.user_id, request, request_hash)
        if storage.advance(session, caller.user_id, request) is None:
            raise Conflict()
        return shape(storage.read(session, caller.user_id, request.id), known)


def read(caller, identifier, revision=None):
    with unit_of_work(scope(caller), read_only=True) as session:
        row = storage.read(session, caller.user_id, identifier, revision)
        if row is None:
            raise NotFound()
        return shape(row, tools())


def list_owned(caller, after, limit):
    with unit_of_work(scope(caller), read_only=True) as session:
        rows = storage.list_owned(session, caller.user_id, after, limit)
        known = tools()
        return {
            "items": [shape(row, known, summary=True) for row in rows[:limit]],
            "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
        }
